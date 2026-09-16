"""
Simula o despacho em lote por (CD, Nível de rota) e calcula o tempo de
entrega informado ao cliente como:

    Tempo_Entrega_Dias = dias_espera_no_CD (até o lote sair)
                        + dias_viagem (distância real / velocidade)

Regra de despacho do lote (por CD + Nivel_Rota, processado em ordem cronológica
de Data_Venda):
    - Um lote fica "aberto" acumulando pedidos.
    - Se o lote atingir a capacidade estimada (baseada no volume histórico do
      grupo) ANTES do prazo máximo (SLA), ele despacha na hora.
    - Se o prazo máximo (SLA) for atingido primeiro, o lote despacha mesmo
      incompleto.
    - Todo pedido do lote "sai" na mesma data de despacho.

SLA (prazo máximo) por nível de rota, conforme informado:
    Dentro da capital     -> 1 dia (sai no dia seguinte)
    Região metropolitana  -> 3 dias
    Interior do estado    -> 5 dias
    Estado vizinho        -> 5 dias (comboio de carretas)

Capacidade de despacho antecipado: estimada automaticamente como
CAPACIDADE_MULTIPLICADOR x a média histórica de pedidos/dia daquele grupo
(CD + nível de rota) — ajustável via --capacidade-mult.

Requisitos: nenhum pacote extra.

Uso:
    python simular_despacho_lotes.py caminho_do_banco.db
    python simular_despacho_lotes.py caminho_do_banco.db --capacidade-mult 2.0
"""

import sqlite3
import argparse
import random
from math import cos, radians, sin, asin, sqrt
from datetime import date, datetime
from collections import defaultdict

TABELA_CLIENTES = "clientes"
TABELA_ENDERECOS = "enderecos"
TABELA_CD = "centros_distribuicao"
TABELA_VENDAS = "vendas"

SLA_DIAS = {
    "Dentro da capital": 1,
    "Região metropolitana": 3,
    "Interior do estado": 5,
    "Estado vizinho": 5,
}

VELOCIDADE_KM_DIA = {
    "Dentro da capital": 500,
    "Região metropolitana": 500,
    "Interior do estado": 450,
    "Estado vizinho": 450,
}

DESVIO_RUIDO_DIAS = 0.5


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


def parse_data(s):
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def preparar_schema(conn):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({TABELA_VENDAS})")
    colunas = [c[1] for c in cur.fetchall()]
    for col in ("Tempo_Entrega_Dias", "Data_Saida_CD", "Dias_Espera_CD", "Dias_Viagem"):
        if col not in colunas:
            tipo = "TEXT" if col == "Data_Saida_CD" else "INTEGER"
            cur.execute(f"ALTER TABLE {TABELA_VENDAS} ADD COLUMN {col} {tipo}")
    conn.commit()


def carregar_contexto_clientes(conn):
    cur = conn.cursor()
    cur.execute(f"""
        SELECT cl.ID_Cliente, cl.Nivel_Rota, cl.CD_Atribuido, e.Latitude, e.Longitude
        FROM {TABELA_CLIENTES} cl
        JOIN {TABELA_ENDERECOS} e ON e.ID_Endereco = cl.ID_Endereco
    """)
    ctx = {}
    for id_cliente, nivel_rota, cd_uf, lat, lon in cur.fetchall():
        ctx[id_cliente] = (nivel_rota, cd_uf, lat, lon)

    cur.execute(f"SELECT UF, Latitude, Longitude FROM {TABELA_CD}")
    cds = {uf: (lat, lon) for uf, lat, lon in cur.fetchall()}
    return ctx, cds


def estimar_capacidades(pedidos_por_grupo, multiplicador, capacidade_minima):
    """Capacidade = maior valor entre (multiplicador x média histórica diária)
    e uma capacidade mínima realista (nenhum caminhão real sai com só 2-3 pacotes)."""
    capacidades = {}
    for grupo, pedidos in pedidos_por_grupo.items():
        dias_distintos = len({p[0] for p in pedidos})  # p[0] = data
        media_diaria = len(pedidos) / max(dias_distintos, 1)
        capacidade_historica = round(media_diaria * multiplicador)
        capacidades[grupo] = max(capacidade_minima, capacidade_historica, 1)
    return capacidades


def simular_lotes(pedidos_por_grupo, capacidades):
    """Retorna dict id_pedido -> (data_saida, dias_espera)."""
    resultado = {}
    for grupo, pedidos in pedidos_por_grupo.items():
        nivel_rota = grupo[1]
        sla = SLA_DIAS.get(nivel_rota, 5)
        capacidade = capacidades[grupo]

        pedidos_ordenados = sorted(pedidos, key=lambda p: p[0])  # por data

        lote = []
        data_abertura_lote = None
        for data_venda, id_pedido in pedidos_ordenados:
            if data_abertura_lote is None:
                data_abertura_lote = data_venda

            # se o prazo já estourou antes de incluir esse pedido, despacha o lote atual primeiro
            if lote and (data_venda - data_abertura_lote).days >= sla:
                data_saida = data_abertura_lote + __import__("datetime").timedelta(days=sla)
                for d_venda, pid in lote:
                    resultado[pid] = (data_saida, (data_saida - d_venda).days)
                lote = []
                data_abertura_lote = data_venda

            lote.append((data_venda, id_pedido))

            if len(lote) >= capacidade:
                data_saida = data_venda  # encheu, sai na hora
                for d_venda, pid in lote:
                    resultado[pid] = (data_saida, (data_saida - d_venda).days)
                lote = []
                data_abertura_lote = None

        # o que sobrou no fim, despacha no prazo máximo a partir da abertura
        if lote:
            data_saida = data_abertura_lote + __import__("datetime").timedelta(days=sla)
            for d_venda, pid in lote:
                resultado[pid] = (data_saida, (data_saida - d_venda).days)

    return resultado


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    parser.add_argument("--capacidade-mult", type=float, default=1.5,
                         help="multiplicador sobre a média histórica de pedidos/dia para considerar 'lote cheio' (padrão 1.5)")
    parser.add_argument("--capacidade-minima", type=int, default=15,
                         help="capacidade mínima realista de um lote/veículo, independente do volume histórico "
                              "(evita lotes de 2-3 pedidos saindo sem esperar o SLA; padrão 15)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    preparar_schema(conn)

    print("Carregando contexto de clientes (rota, CD, coordenadas)...")
    ctx_clientes, cds = carregar_contexto_clientes(conn)

    cur = conn.cursor()
    cur.execute(f"SELECT ID_Pedido, ID_Cliente, Data_Venda FROM {TABELA_VENDAS}")
    pedidos_raw = cur.fetchall()

    pedidos_por_grupo = defaultdict(list)
    pedido_para_cliente = {}
    for id_pedido, id_cliente, data_venda_str in pedidos_raw:
        info = ctx_clientes.get(id_cliente)
        if info is None:
            continue
        nivel_rota, cd_uf, lat, lon = info
        if nivel_rota is None or cd_uf not in cds:
            continue
        data_venda = parse_data(data_venda_str)
        pedidos_por_grupo[(cd_uf, nivel_rota)].append((data_venda, id_pedido))
        pedido_para_cliente[id_pedido] = id_cliente

    print(f"{len(pedidos_por_grupo)} grupos (CD x nível de rota) encontrados.")

    capacidades = estimar_capacidades(pedidos_por_grupo, args.capacidade_mult, args.capacidade_minima)
    print("Capacidades de despacho estimadas (pedidos p/ lote cheio):")
    for grupo, cap in sorted(capacidades.items()):
        print(f"  {grupo[0]} / {grupo[1]}: {cap}")

    print("Simulando despacho em lote...")
    despachos = simular_lotes(pedidos_por_grupo, capacidades)

    print("Calculando tempo de viagem e tempo total de entrega...")
    atualizacoes = []
    for id_pedido, (data_saida, dias_espera) in despachos.items():
        id_cliente = pedido_para_cliente[id_pedido]
        nivel_rota, cd_uf, lat, lon = ctx_clientes[id_cliente]
        lat_cd, lon_cd = cds[cd_uf]
        dist_km = haversine_km(lat, lon, lat_cd, lon_cd)

        velocidade = VELOCIDADE_KM_DIA.get(nivel_rota, 450)
        dias_viagem = max(0, round(dist_km / velocidade))

        ruido = round(random.gauss(0, DESVIO_RUIDO_DIAS))
        tempo_total = max(1, dias_espera + dias_viagem + ruido)

        atualizacoes.append((tempo_total, data_saida.isoformat(), dias_espera, dias_viagem, id_pedido))

    cur.executemany(f"""
        UPDATE {TABELA_VENDAS}
        SET Tempo_Entrega_Dias = ?, Data_Saida_CD = ?, Dias_Espera_CD = ?, Dias_Viagem = ?
        WHERE ID_Pedido = ?
    """, atualizacoes)
    conn.commit()

    print(f"Concluído. {len(atualizacoes)} pedidos atualizados.")
    conn.close()


if __name__ == "__main__":
    main()
