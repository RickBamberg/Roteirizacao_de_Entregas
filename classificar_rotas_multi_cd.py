"""
Versão 3: separa a geração de endereços (roda uma única vez) da classificação
de rota por Centro de Distribuição (roda quantas vezes precisar, um CD por vez,
ou todos de uma vez com --cds).

Cada cliente é associado ao CD mais adequado dentre os informados, nesta ordem
de prioridade:
    1. CD na própria UF do cliente (sempre vence, se existir)
    2. CD em UF vizinha à do cliente, escolhendo o de MENOR distância até a
       capital do CD (aproximação simples de "CD mais próximo")
    3. Se nenhum CD atende (nem mesma UF, nem vizinha), cliente fica em
       "Sem CD (fora de área)" — sinaliza que falta CD pra essa região

Níveis de rota, iguais à v2:
    Dentro da capital / Região metropolitana / Interior do estado / Estado vizinho

Requisitos:
    pip install faker --break-system-packages

Uso:
    # 1) gerar endereços (uma vez só, se ainda não gerou)
    python gerar_enderecos_ficticios_v2.py caminho_do_banco.db --cd-uf BA

    # 2) classificar TODOS os clientes considerando vários CDs de uma vez
    python classificar_rotas_multi_cd.py caminho_do_banco.db --cds BA,CE,SP,SC,AM
"""

import sqlite3
import argparse
from math import cos, radians, sin, asin, sqrt

TABELA_CLIENTES = "clientes"
TABELA_CIDADES = "cidades"

ADJACENCIA_UF = {
    "AC": ["AM", "RO"],
    "AL": ["PE", "SE", "BA"],
    "AP": ["PA"],
    "AM": ["RR", "PA", "MT", "RO", "AC"],
    "BA": ["SE", "AL", "PE", "PI", "TO", "GO", "MG", "ES"],
    "CE": ["PI", "PE", "PB", "RN"],
    "DF": ["GO"],
    "ES": ["BA", "MG", "RJ"],
    "GO": ["MT", "MS", "MG", "BA", "TO", "DF"],
    "MA": ["PI", "TO", "PA"],
    "MT": ["RO", "AM", "PA", "TO", "GO", "MS"],
    "MS": ["MT", "GO", "MG", "SP", "PR"],
    "MG": ["BA", "GO", "SP", "RJ", "ES", "MS"],
    "PA": ["AP", "MA", "TO", "MT", "AM", "RR"],
    "PB": ["RN", "CE", "PE"],
    "PR": ["SP", "MS", "SC"],
    "PE": ["PB", "CE", "PI", "BA", "AL"],
    "PI": ["MA", "CE", "PE", "BA", "TO"],
    "RJ": ["MG", "ES", "SP"],
    "RN": ["CE", "PB"],
    "RS": ["SC"],
    "RO": ["AM", "MT", "AC"],
    "RR": ["AM", "PA"],
    "SC": ["PR", "RS"],
    "SP": ["MG", "RJ", "PR", "MS"],
    "SE": ["AL", "BA"],
    "TO": ["MA", "PI", "BA", "GO", "MT", "PA"],
}

RAIO_METROPOLITANO_KM = 60


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


def preparar_schema(conn):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({TABELA_CLIENTES})")
    colunas = [c[1] for c in cur.fetchall()]
    if "Nivel_Rota" not in colunas:
        cur.execute(f"ALTER TABLE {TABELA_CLIENTES} ADD COLUMN Nivel_Rota TEXT")
    if "CD_Atribuido" not in colunas:
        cur.execute(f"ALTER TABLE {TABELA_CLIENTES} ADD COLUMN CD_Atribuido TEXT")
    conn.commit()


def carregar_capitais(conn, ufs_cd):
    cur = conn.cursor()
    cur.execute(f"SELECT UF, Latitude, Longitude FROM {TABELA_CIDADES} WHERE Capital = 1")
    capitais = {uf: (lat, lon) for uf, lat, lon in cur.fetchall()}
    faltando = [uf for uf in ufs_cd if uf not in capitais]
    if faltando:
        raise ValueError(f"Não achei capital cadastrada para: {faltando}")
    return capitais


def escolher_cd(uf_cliente, lat_cliente, lon_cliente, ufs_cd, capitais):
    # 1) CD na própria UF sempre vence
    if uf_cliente in ufs_cd:
        return uf_cliente

    # 2) entre os CDs vizinhos da UF do cliente, pega o mais próximo por distância real
    candidatos = [uf for uf in ADJACENCIA_UF.get(uf_cliente, []) if uf in ufs_cd]
    if not candidatos:
        return None
    melhor_uf, melhor_dist = None, float("inf")
    for uf_cd in candidatos:
        lat_cd, lon_cd = capitais[uf_cd]
        d = haversine_km(lat_cliente, lon_cliente, lat_cd, lon_cd)
        if d < melhor_dist:
            melhor_uf, melhor_dist = uf_cd, d
    return melhor_uf


def classificar_rota(uf_cliente, is_capital_cliente, dist_ate_capital_cd, uf_cd):
    if uf_cliente == uf_cd:
        if is_capital_cliente:
            return "Dentro da capital"
        if dist_ate_capital_cd is not None and dist_ate_capital_cd <= RAIO_METROPOLITANO_KM:
            return "Região metropolitana"
        return "Interior do estado"
    return "Estado vizinho"


def rodar(conn, ufs_cd):
    cur = conn.cursor()
    capitais = carregar_capitais(conn, ufs_cd)

    cur.execute(f"""
        SELECT cl.ID_Cliente, ci.UF, ci.Capital, e.Latitude, e.Longitude
        FROM {TABELA_CLIENTES} cl
        JOIN {TABELA_CIDADES} ci ON ci.ID_Cidade = cl.ID_Cidade
        JOIN enderecos e ON e.ID_Endereco = cl.ID_Endereco
    """)
    linhas = cur.fetchall()
    print(f"Classificando {len(linhas)} clientes entre {len(ufs_cd)} CDs: {', '.join(ufs_cd)}")

    atualizacoes = []
    sem_cd = 0
    for id_cliente, uf, capital, lat, lon in linhas:
        cd_escolhido = escolher_cd(uf, lat, lon, ufs_cd, capitais)
        if cd_escolhido is None:
            atualizacoes.append(("Sem CD (fora de área)", None, id_cliente))
            sem_cd += 1
            continue
        lat_cd, lon_cd = capitais[cd_escolhido]
        dist = haversine_km(lat, lon, lat_cd, lon_cd)
        nivel = classificar_rota(uf, capital == 1, dist, cd_escolhido)
        atualizacoes.append((nivel, cd_escolhido, id_cliente))

    cur.executemany(f"""
        UPDATE {TABELA_CLIENTES} SET Nivel_Rota = ?, CD_Atribuido = ? WHERE ID_Cliente = ?
    """, atualizacoes)
    conn.commit()

    print(f"Concluído. {sem_cd} clientes ficaram sem CD (regiões ainda não cobertas).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    parser.add_argument("--cds", required=True, help="lista de UFs com CD, separadas por vírgula (ex: BA,CE,SP)")
    args = parser.parse_args()

    ufs_cd = [uf.strip().upper() for uf in args.cds.split(",")]

    conn = sqlite3.connect(args.db_path)
    preparar_schema(conn)
    rodar(conn, ufs_cd)
    conn.close()


if __name__ == "__main__":
    main()
