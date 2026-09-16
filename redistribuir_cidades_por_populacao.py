"""
Redistribui os clientes de um banco SQLite entre municípios REAIS do Brasil,
ponderando pela população de cada cidade, e reconstrói a tabela `cidades`
com dados enriquecidos (latitude, longitude, população, capital).

Resolve o problema de concentração artificial (ex: 604 clientes da BA,
todos em Salvador) usando a base pública de municípios do IBGE mantida em
https://github.com/mapaslivres/municipios-br

Passos:
  1. Baixa (ou usa cache local de) municipios.csv com lat/lon/população/UF.
  2. Recria a tabela `cidades` só com os municípios das UFs já presentes
     no seu banco, mantendo o UF original de cada cliente (não muda o
     estado, só a cidade dentro do estado).
  3. Reatribui cada cliente a um município real da mesma UF, sorteado
     com peso proporcional à população (cidades maiores recebem mais
     clientes, do jeito que acontece na vida real).
  4. Zera os endereços fictícios antigos (tabela enderecos / ID_Endereco)
     para que você rode de novo o script `gerar_enderecos_ficticios.py`
     — agora com cidades de verdade, o resultado vai refletir melhor a
     hierarquia de rotas (capital / metropolitana / interior / vizinhos).

Requisitos:
    pip install pandas requests --break-system-packages

Uso:
    python redistribuir_cidades_por_populacao.py caminho_do_banco.db
"""

import sqlite3
import sys
import random
import argparse
import os

import pandas as pd
import requests

MUNICIPIOS_URL = "https://raw.githubusercontent.com/mapaslivres/municipios-br/main/tabelas/municipios.csv"
CACHE_LOCAL = "municipios_brasil.csv"

TABELA_CLIENTES = "clientes"
TABELA_CIDADES = "cidades"
COL_ID_CLIENTE = "ID_Cliente"
COL_ID_CIDADE = "ID_Cidade"
COL_CIDADE = "Cidade"
COL_UF = "UF"

UFS_VALIDAS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
}


def baixar_municipios():
    if os.path.exists(CACHE_LOCAL):
        print(f"Usando cache local: {CACHE_LOCAL}")
        return pd.read_csv(CACHE_LOCAL)

    print(f"Baixando base de municípios de {MUNICIPIOS_URL} ...")
    resp = requests.get(MUNICIPIOS_URL, timeout=60)
    resp.raise_for_status()
    with open(CACHE_LOCAL, "wb") as f:
        f.write(resp.content)
    print("Download concluído, salvo em cache local.")
    return pd.read_csv(CACHE_LOCAL)


def detectar_colunas(df):
    """Detecta as colunas relevantes sem depender da ordem exata do header.
    Usa correspondência de PALAVRA INTEIRA (via split por não-alfanuméricos),
    não substring — evita, por exemplo, casar 'lat' dentro de 'relation_id'."""
    import re

    uf_col = None
    for col in df.columns:
        amostra = df[col].dropna().astype(str).str.upper()
        if amostra.isin(UFS_VALIDAS).mean() > 0.9:
            uf_col = col
            break
    if uf_col is None:
        raise ValueError("Não consegui identificar a coluna de UF no CSV baixado.")

    def achar(*termos):
        termos = set(termos)
        for col in df.columns:
            tokens = set(re.split(r"[^a-z0-9]+", col.lower()))
            if tokens & termos:
                return col
        return None

    nome_col = achar("name", "nome")
    lat_col = achar("lat", "latitude")
    lon_col = achar("lon", "longitude", "lng")
    pop_col = achar("pop", "populacao", "população")
    capital_col = achar("capital")

    faltando = [n for n, v in [("nome", nome_col), ("lat", lat_col), ("lon", lon_col),
                                ("pop", pop_col)] if v is None]
    if faltando:
        raise ValueError(f"Não encontrei colunas para: {faltando}. Colunas disponíveis: {list(df.columns)}")

    # Sanity check: coordenadas do Brasil ficam entre lat -35/+6 e lon -75/-32.
    # Se a coluna detectada estiver fora disso, é sinal de que pegamos a coluna errada.
    lat_amostra = pd.to_numeric(df[lat_col], errors="coerce").dropna()
    lon_amostra = pd.to_numeric(df[lon_col], errors="coerce").dropna()
    if not lat_amostra.between(-35, 6).mean() > 0.95:
        raise ValueError(
            f"Coluna de latitude detectada ('{lat_col}') tem valores fora da faixa do Brasil "
            f"(-35 a 6). Exemplo: {lat_amostra.iloc[0]}. Verifique o CSV manualmente."
        )
    if not lon_amostra.between(-75, -32).mean() > 0.95:
        raise ValueError(
            f"Coluna de longitude detectada ('{lon_col}') tem valores fora da faixa do Brasil "
            f"(-75 a -32). Exemplo: {lon_amostra.iloc[0]}. Verifique o CSV manualmente."
        )

    return {"uf": uf_col, "nome": nome_col, "lat": lat_col, "lon": lon_col,
            "pop": pop_col, "capital": capital_col}


def recriar_cidades(conn, municipios_df, cols, ufs_alvo):
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {TABELA_CIDADES}_novo")
    cur.execute(f"""
        CREATE TABLE {TABELA_CIDADES}_novo (
            {COL_ID_CIDADE} INTEGER PRIMARY KEY,
            {COL_CIDADE} TEXT,
            {COL_UF} TEXT,
            Populacao INTEGER,
            Capital INTEGER,
            Latitude REAL,
            Longitude REAL
        )
    """)

    df = municipios_df[municipios_df[cols["uf"]].astype(str).str.upper().isin(ufs_alvo)].copy()
    df = df.reset_index(drop=True)
    df["_novo_id"] = range(1, len(df) + 1)

    registros = [
        (
            int(row["_novo_id"]),
            str(row[cols["nome"]]),
            str(row[cols["uf"]]).upper(),
            int(row[cols["pop"]]) if pd.notna(row[cols["pop"]]) else 0,
            int(bool(row[cols["capital"]])) if cols["capital"] and pd.notna(row[cols["capital"]]) else 0,
            float(row[cols["lat"]]),
            float(row[cols["lon"]]),
        )
        for _, row in df.iterrows()
    ]
    cur.executemany(f"""
        INSERT INTO {TABELA_CIDADES}_novo
            ({COL_ID_CIDADE}, {COL_CIDADE}, {COL_UF}, Populacao, Capital, Latitude, Longitude)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, registros)

    cur.execute(f"DROP TABLE {TABELA_CIDADES}")
    cur.execute(f"ALTER TABLE {TABELA_CIDADES}_novo RENAME TO {TABELA_CIDADES}")
    conn.commit()

    print(f"Tabela {TABELA_CIDADES} reconstruída com {len(registros)} municípios reais "
          f"(UFs: {', '.join(sorted(ufs_alvo))}).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    cur = conn.cursor()

    print("1) Capturando UF atual de cada cliente (antes de mexer na tabela cidades)...")
    cur.execute(f"""
        SELECT cl.{COL_ID_CLIENTE}, ci.{COL_UF}
        FROM {TABELA_CLIENTES} cl
        JOIN {TABELA_CIDADES} ci ON ci.{COL_ID_CIDADE} = cl.{COL_ID_CIDADE}
    """)
    cliente_uf = cur.fetchall()
    ufs_presentes = {uf for _, uf in cliente_uf}
    print(f"   {len(cliente_uf)} clientes em {len(ufs_presentes)} UFs distintas.")

    print("2) Carregando base de municípios do IBGE...")
    municipios_df = baixar_municipios()
    cols = detectar_colunas(municipios_df)

    print("3) Reconstruindo tabela cidades com municípios reais...")
    recriar_cidades(conn, municipios_df, cols, ufs_presentes)

    print("4) Sorteando, para cada cliente, um município real da mesma UF (peso = população)...")
    cur.execute(f"SELECT {COL_ID_CIDADE}, {COL_UF}, Populacao FROM {TABELA_CIDADES}")
    cidades_por_uf = {}
    for id_cidade, uf, pop in cur.fetchall():
        cidades_por_uf.setdefault(uf, []).append((id_cidade, max(pop, 1)))

    atualizacoes = []
    for id_cliente, uf in cliente_uf:
        opcoes = cidades_por_uf.get(uf)
        if not opcoes:
            continue
        ids, pesos = zip(*opcoes)
        novo_id_cidade = random.choices(ids, weights=pesos, k=1)[0]
        atualizacoes.append((novo_id_cidade, id_cliente))

    cur.executemany(
        f"UPDATE {TABELA_CLIENTES} SET {COL_ID_CIDADE} = ? WHERE {COL_ID_CLIENTE} = ?",
        atualizacoes,
    )
    conn.commit()
    print(f"   {len(atualizacoes)} clientes redistribuídos.")

    print("5) Limpando endereços fictícios antigos (cidade mudou, endereço precisa ser refeito)...")
    cur.execute("DROP TABLE IF EXISTS enderecos")
    cur.execute(f"PRAGMA table_info({TABELA_CLIENTES})")
    colunas = [c[1] for c in cur.fetchall()]
    if "ID_Endereco" in colunas:
        cur.execute(f"UPDATE {TABELA_CLIENTES} SET ID_Endereco = NULL")
    conn.commit()
    print("   Pronto. Rode novamente gerar_enderecos_ficticios.py para recriar os endereços"
          " com base nas cidades reais.")

    conn.close()


if __name__ == "__main__":
    main()
