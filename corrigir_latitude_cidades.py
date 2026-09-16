"""
Corrige a coluna Latitude da tabela `cidades` (que ficou errada por causa do
bug de detecção de coluna — pegou 'osm_relation_id' em vez de 'lat').

NÃO reembaralha a atribuição de cidade dos clientes: casa cada cidade já
existente em `cidades` (por Cidade+UF) com a linha correspondente no CSV de
municípios e atualiza só a Latitude.

Depois de rodar este script, você precisa regenerar (porque dependiam da
Latitude errada):
    1. enderecos / ID_Endereco  -> rode de novo gerar_enderecos_ficticios_v2.py
       (antes, limpe: DELETE FROM enderecos; UPDATE clientes SET ID_Endereco=NULL, Nivel_Rota=NULL, CD_Atribuido=NULL;)
    2. centros_distribuicao      -> rode de novo criar_centros_distribuicao.py
    3. classificação de rota     -> rode de novo classificar_rotas_multi_cd.py

Uso:
    python corrigir_latitude_cidades.py caminho_do_banco.db
"""

import sqlite3
import argparse
import re

import pandas as pd

CACHE_LOCAL = "municipios_brasil.csv"
TABELA_CIDADES = "cidades"

UFS_VALIDAS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
}


def detectar_colunas(df):
    uf_col = None
    for col in df.columns:
        amostra = df[col].dropna().astype(str).str.upper()
        if amostra.isin(UFS_VALIDAS).mean() > 0.9:
            uf_col = col
            break
    if uf_col is None:
        raise ValueError("Não consegui identificar a coluna de UF no CSV.")

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

    lat_amostra = pd.to_numeric(df[lat_col], errors="coerce").dropna()
    if not lat_amostra.between(-35, 6).mean() > 0.95:
        raise ValueError(f"Coluna de latitude '{lat_col}' fora da faixa do Brasil. Confira manualmente.")

    return {"uf": uf_col, "nome": nome_col, "lat": lat_col, "lon": lon_col}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    args = parser.parse_args()

    print("Carregando base de municípios (usa o cache local se existir)...")
    municipios_df = pd.read_csv(CACHE_LOCAL)
    cols = detectar_colunas(municipios_df)
    print(f"Colunas detectadas: {cols}")

    lookup = {
        (str(row[cols["uf"]]).upper(), str(row[cols["nome"]])): float(row[cols["lat"]])
        for _, row in municipios_df.iterrows()
    }

    conn = sqlite3.connect(args.db_path)
    cur = conn.cursor()
    cur.execute(f"SELECT ID_Cidade, Cidade, UF FROM {TABELA_CIDADES}")
    cidades = cur.fetchall()

    atualizacoes = []
    nao_encontrados = []
    for id_cidade, cidade, uf in cidades:
        lat_correta = lookup.get((uf, cidade))
        if lat_correta is None:
            nao_encontrados.append((cidade, uf))
            continue
        atualizacoes.append((lat_correta, id_cidade))

    cur.executemany(f"UPDATE {TABELA_CIDADES} SET Latitude = ? WHERE ID_Cidade = ?", atualizacoes)
    conn.commit()

    print(f"{len(atualizacoes)} cidades corrigidas.")
    if nao_encontrados:
        print(f"AVISO: {len(nao_encontrados)} cidades não foram encontradas no CSV "
              f"(nome pode ter acento/grafia diferente): {nao_encontrados[:10]}...")

    conn.close()


if __name__ == "__main__":
    main()
