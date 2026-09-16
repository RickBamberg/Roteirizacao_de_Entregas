"""
Versão 2 do gerador de endereços fictícios.

Diferenças em relação à v1 (gerar_enderecos_ficticios.py):
  - Não chama mais o Nominatim: usa Latitude/Longitude que já vêm na
    tabela `cidades` (preenchidas pelo script redistribuir_cidades_por_populacao.py).
  - O raio de dispersão (jitter) escala com o tamanho da cidade: capitais
    e cidades grandes espalham mais (pessoas moram mais longe do centro),
    cidades pequenas espalham menos.
  - Também classifica cada cliente em um nível de rota, relativo a um
    Centro de Distribuição (CD) que você informa via UF:
        "Dentro da capital"       -> cliente está na própria capital do CD
        "Região metropolitana"    -> mesma UF do CD, dentro do raio metropolitano
        "Interior do estado"      -> mesma UF do CD, fora do raio metropolitano
        "Estado vizinho"          -> UF diferente, mas faz fronteira com a UF do CD
        "Fora da área do CD"      -> não é nem mesma UF nem vizinha (precisa de outro CD)

Requisitos:
    pip install faker --break-system-packages   (geopy não é mais necessário)

Uso:
    python gerar_enderecos_ficticios_v2.py caminho_do_banco.db --cd-uf BA
"""

import sqlite3
import sys
import random
import argparse
from math import cos, radians, sin, asin, sqrt

from faker import Faker

fake = Faker("pt_BR")

TABELA_CLIENTES = "clientes"
TABELA_CIDADES = "cidades"

# Fronteiras reais entre estados brasileiros (validado por simetria)
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


def preparar_schema(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS enderecos (
            ID_Endereco INTEGER PRIMARY KEY AUTOINCREMENT,
            Logradouro TEXT,
            Numero TEXT,
            Complemento TEXT,
            Bairro TEXT,
            CEP TEXT,
            ID_Cidade INTEGER,
            Latitude REAL,
            Longitude REAL
        )
    """)
    cur.execute(f"PRAGMA table_info({TABELA_CLIENTES})")
    colunas = [c[1] for c in cur.fetchall()]
    if "ID_Endereco" not in colunas:
        cur.execute(f"ALTER TABLE {TABELA_CLIENTES} ADD COLUMN ID_Endereco INTEGER")
    if "Nivel_Rota" not in colunas:
        cur.execute(f"ALTER TABLE {TABELA_CLIENTES} ADD COLUMN Nivel_Rota TEXT")
    conn.commit()


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


def raio_jitter_km(populacao):
    # cidades maiores "espalham" mais (aprox. raiz da população, limitado a 25km)
    return min(25.0, max(1.0, (populacao ** 0.5) / 150))


def jitter_coords(lat, lon, raio_km):
    delta_lat = random.uniform(-raio_km, raio_km) / 111.0
    fator_lon = max(abs(cos(radians(lat))), 0.1)
    delta_lon = random.uniform(-raio_km, raio_km) / (111.0 * fator_lon)
    return lat + delta_lat, lon + delta_lon


def classificar_rota(uf_cliente, is_capital_cliente, dist_ate_capital_cd, uf_cd):
    if uf_cliente == uf_cd:
        if is_capital_cliente:
            return "Dentro da capital"
        if dist_ate_capital_cd is not None and dist_ate_capital_cd <= RAIO_METROPOLITANO_KM:
            return "Região metropolitana"
        return "Interior do estado"
    if uf_cliente in ADJACENCIA_UF.get(uf_cd, []):
        return "Estado vizinho"
    return "Fora da área do CD"


def gerar_enderecos_e_classificar(conn, uf_cd):
    cur = conn.cursor()

    cur.execute(f"""
        SELECT ID_Cidade, UF, Populacao, Capital, Latitude, Longitude
        FROM {TABELA_CIDADES}
    """)
    cidades = {row[0]: row[1:] for row in cur.fetchall()}

    capital_cd = next(
        ((lat, lon) for (uf, pop, cap, lat, lon) in cidades.values() if uf == uf_cd and cap == 1),
        None,
    )
    if capital_cd is None:
        print(f"AVISO: não encontrei a capital cadastrada para UF={uf_cd}; "
              "distâncias à capital do CD ficarão vazias.")

    cur.execute(f"""
        SELECT {TABELA_CLIENTES}.ID_Cliente, {TABELA_CLIENTES}.ID_Cidade
        FROM {TABELA_CLIENTES}
        WHERE ID_Endereco IS NULL
    """)
    clientes = cur.fetchall()
    print(f"Gerando endereços + classificando rota para {len(clientes)} clientes "
          f"(CD de referência: {uf_cd})...")

    for id_cliente, id_cidade in clientes:
        uf, pop, capital, lat_centro, lon_centro = cidades[id_cidade]
        raio = raio_jitter_km(pop)
        lat, lon = jitter_coords(lat_centro, lon_centro, raio)

        dist_ate_capital_cd = None
        if capital_cd is not None:
            dist_ate_capital_cd = haversine_km(lat, lon, capital_cd[0], capital_cd[1])

        nivel_rota = classificar_rota(uf, capital == 1, dist_ate_capital_cd, uf_cd)

        logradouro = fake.street_name()
        numero = str(fake.building_number())
        complemento = random.choice([None, None, f"Apto {random.randint(1, 200)}", "Casa"])
        bairro = fake.bairro()
        cep = fake.postcode()

        cur.execute("""
            INSERT INTO enderecos
                (Logradouro, Numero, Complemento, Bairro, CEP, ID_Cidade, Latitude, Longitude)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (logradouro, numero, complemento, bairro, cep, id_cidade, lat, lon))
        id_endereco = cur.lastrowid

        cur.execute(f"""
            UPDATE {TABELA_CLIENTES}
            SET ID_Endereco = ?, Nivel_Rota = ?
            WHERE ID_Cliente = ?
        """, (id_endereco, nivel_rota, id_cliente))

    conn.commit()
    print("Concluído.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    parser.add_argument("--cd-uf", required=True,
                         help="UF onde fica o centro de distribuição de referência (ex: BA)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    preparar_schema(conn)
    gerar_enderecos_e_classificar(conn, args.cd_uf.upper())
    conn.close()


if __name__ == "__main__":
    main()
