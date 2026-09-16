"""
Agrega os pedidos em `vendas` numa série semanal por (CD_Atribuido, Nivel_Rota),
criando a tabela `demanda_semanal` — essa é a base de treino para o modelo de
previsão de demanda (quantos pedidos esperar por CD/rota na próxima semana).

Cada linha = 1 (CD, Nivel_Rota, Ano, Semana) com a contagem de pedidos e, se
a tabela itens_vendas existir, também o valor total vendido no período.

Requisitos: nenhum pacote extra.

Uso:
    python preparar_demanda_semanal.py caminho_do_banco.db
"""

import sqlite3
import argparse
from datetime import datetime, date

TABELA_VENDAS = "vendas"
TABELA_CLIENTES = "clientes"
TABELA_DEMANDA = "demanda_semanal"


def semana_iso(data_str):
    d = datetime.strptime(data_str[:10], "%Y-%m-%d").date()
    ano_iso, semana_iso, _ = d.isocalendar()
    return ano_iso, semana_iso


def criar_tabela(conn):
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {TABELA_DEMANDA}")
    cur.execute(f"""
        CREATE TABLE {TABELA_DEMANDA} (
            CD_Atribuido TEXT,
            Nivel_Rota TEXT,
            Ano_ISO INTEGER,
            Semana_ISO INTEGER,
            Data_Inicio_Semana TEXT,
            Qtd_Pedidos INTEGER,
            PRIMARY KEY (CD_Atribuido, Nivel_Rota, Ano_ISO, Semana_ISO)
        )
    """)
    conn.commit()


def agregar(conn):
    from datetime import timedelta

    cur = conn.cursor()
    cur.execute(f"""
        SELECT cl.CD_Atribuido, cl.Nivel_Rota, v.Data_Venda
        FROM {TABELA_VENDAS} v
        JOIN {TABELA_CLIENTES} cl ON cl.ID_Cliente = v.ID_Cliente
        WHERE cl.CD_Atribuido IS NOT NULL AND cl.Nivel_Rota IS NOT NULL
    """)
    linhas = cur.fetchall()
    print(f"Agregando {len(linhas)} pedidos em séries semanais...")

    # referência: qualquer segunda-feira conhecida, para achar o "resto" que identifica segundas-feiras
    REMAINDER = date(2000, 1, 3).toordinal() % 7  # 2000-01-03 é uma segunda-feira

    def segunda_da_semana(data_str):
        d = datetime.strptime(data_str[:10], "%Y-%m-%d").date()
        return d - timedelta(days=d.weekday())  # volta para a segunda-feira daquela semana

    def ordinal_da_segunda(segunda):
        return segunda.toordinal() // 7  # consecutivo e sem ambiguidade de virada de ano

    def segunda_do_ordinal(ordinal):
        return date.fromordinal(ordinal * 7 + REMAINDER)

    contagem = {}
    intervalo_por_grupo = {}

    for cd, nivel, data_venda in linhas:
        segunda = segunda_da_semana(data_venda)
        ordinal = ordinal_da_segunda(segunda)
        chave = (cd, nivel, ordinal)
        contagem[chave] = contagem.get(chave, 0) + 1

        grupo = (cd, nivel)
        atual = intervalo_por_grupo.get(grupo)
        if atual is None:
            intervalo_por_grupo[grupo] = [ordinal, ordinal]
        else:
            atual[0] = min(atual[0], ordinal)
            atual[1] = max(atual[1], ordinal)

    # gera o calendário completo (sem buracos) para cada grupo, preenchendo com 0
    registros = []
    for (cd, nivel), (ord_min, ord_max) in intervalo_por_grupo.items():
        for ordinal in range(ord_min, ord_max + 1):
            segunda = segunda_do_ordinal(ordinal)
            ano_iso, semana_iso, _ = segunda.isocalendar()
            qtd = contagem.get((cd, nivel, ordinal), 0)
            registros.append((cd, nivel, ano_iso, semana_iso, segunda.isoformat(), qtd))

    cur.executemany(f"""
        INSERT INTO {TABELA_DEMANDA}
            (CD_Atribuido, Nivel_Rota, Ano_ISO, Semana_ISO, Data_Inicio_Semana, Qtd_Pedidos)
        VALUES (?, ?, ?, ?, ?, ?)
    """, registros)
    conn.commit()

    zeros = sum(1 for r in registros if r[5] == 0)
    print(f"{len(registros)} linhas gravadas em {TABELA_DEMANDA} "
          f"({len(intervalo_por_grupo)} grupos CD x Nivel_Rota, calendário completo sem buracos). "
          f"{zeros} semanas com 0 pedidos preenchidas explicitamente.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    criar_tabela(conn)
    agregar(conn)
    conn.close()


if __name__ == "__main__":
    main()
