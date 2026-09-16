"""
Cria a tabela `centros_distribuicao`, com um registro por CD, usando a
capital de cada UF informada (já com lat/lon reais, vindos da tabela
`cidades` preenchida pelo redistribuir_cidades_por_populacao.py).

Uso:
    python criar_centros_distribuicao.py caminho_do_banco.db --cds BA,CE,SP,SC,AM,PA
"""

import sqlite3
import argparse

TABELA_CIDADES = "cidades"
TABELA_CD = "centros_distribuicao"


def criar_tabela(conn):
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {TABELA_CD}")
    cur.execute(f"""
        CREATE TABLE {TABELA_CD} (
            ID_CD INTEGER PRIMARY KEY AUTOINCREMENT,
            UF TEXT UNIQUE,
            Cidade TEXT,
            Latitude REAL,
            Longitude REAL
        )
    """)
    conn.commit()


def popular(conn, ufs_cd):
    cur = conn.cursor()
    cur.execute(f"SELECT UF, Cidade, Latitude, Longitude FROM {TABELA_CIDADES} WHERE Capital = 1")
    capitais = {uf: (cidade, lat, lon) for uf, cidade, lat, lon in cur.fetchall()}

    faltando = [uf for uf in ufs_cd if uf not in capitais]
    if faltando:
        raise ValueError(f"Não achei capital cadastrada para: {faltando}")

    registros = [(uf, *capitais[uf]) for uf in ufs_cd]
    cur.executemany(f"""
        INSERT INTO {TABELA_CD} (UF, Cidade, Latitude, Longitude) VALUES (?, ?, ?, ?)
    """, registros)
    conn.commit()

    print(f"{len(registros)} centros de distribuição criados:")
    for uf, cidade, lat, lon in registros:
        print(f"  {uf} - {cidade} ({lat:.4f}, {lon:.4f})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    parser.add_argument("--cds", required=True, help="UFs com CD, separadas por vírgula (ex: BA,CE,SP,SC,AM,PA)")
    args = parser.parse_args()

    ufs_cd = [uf.strip().upper() for uf in args.cds.split(",")]

    conn = sqlite3.connect(args.db_path)
    criar_tabela(conn)
    popular(conn, ufs_cd)
    conn.close()


if __name__ == "__main__":
    main()
