"""
Gera endereços fictícios para clientes de um banco SQLite, criando:
  - tabela `enderecos` (Logradouro, Numero, Complemento, Bairro, CEP, Latitude, Longitude, ID_Cidade)
  - coluna `ID_Endereco` em `clientes`, referenciando o endereço gerado

Cada cidade distinta é geocodificada UMA VEZ (não por cliente) via Nominatim/OpenStreetMap.
A coordenada de cada cliente é a do centro da cidade + um deslocamento aleatório
(jitter) dentro de um raio configurável, simulando ruas diferentes.

Requisitos:
    pip install faker geopy --break-system-packages

Uso:
    python gerar_enderecos_ficticios.py caminho_para_seu_banco.db [--raio-km 5]

Observação: ajuste os nomes de tabela/coluna abaixo (TABELA_CLIENTES, COL_ID_CLIENTE,
etc.) se o seu banco real usar nomes diferentes do DER.
"""

import sqlite3
import sys
import time
import random
import argparse
from math import cos, radians

from faker import Faker
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

# ---- ajuste aqui se seus nomes de tabela/coluna forem diferentes ----
TABELA_CLIENTES = "clientes"
TABELA_CIDADES = "cidades"
COL_ID_CLIENTE = "ID_Cliente"
COL_ID_CIDADE = "ID_Cidade"
COL_CIDADE = "Cidade"
COL_UF = "UF"
# ----------------------------------------------------------------------

fake = Faker("pt_BR")


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
    conn.commit()


def geocodificar_cidades(conn, geocode_fn=None):
    """Geocodifica cada (Cidade, UF) única. geocode_fn é injetável para testes."""
    if geocode_fn is None:
        geolocator = Nominatim(user_agent="carlos_roteiro_entrega")
        geocode_fn = RateLimiter(geolocator.geocode, min_delay_seconds=1.1)

    cur = conn.cursor()
    cur.execute(f"SELECT {COL_ID_CIDADE}, {COL_CIDADE}, {COL_UF} FROM {TABELA_CIDADES}")
    cidades = cur.fetchall()

    cache = {}
    for id_cidade, cidade, uf in cidades:
        query = f"{cidade}, {uf}, Brasil"
        try:
            loc = geocode_fn(query)
        except Exception as e:
            print(f"  erro geocodificando {query}: {e}")
            loc = None
        if loc:
            lat, lon = (loc.latitude, loc.longitude) if hasattr(loc, "latitude") else loc
            cache[id_cidade] = (lat, lon)
            print(f"  OK   {cidade}/{uf} -> {lat:.5f}, {lon:.5f}")
        else:
            cache[id_cidade] = (None, None)
            print(f"  FALHOU {cidade}/{uf} — sem coordenada, endereço ficará sem lat/lon")
    return cache


def jitter_coords(lat, lon, raio_km):
    if lat is None or lon is None:
        return None, None
    delta_lat = random.uniform(-raio_km, raio_km) / 111.0
    fator_lon = max(abs(cos(radians(lat))), 0.1)  # evita divisão por ~0 perto dos polos
    delta_lon = random.uniform(-raio_km, raio_km) / (111.0 * fator_lon)
    return lat + delta_lat, lon + delta_lon


def gerar_enderecos(conn, cache_cidades, raio_km):
    cur = conn.cursor()
    cur.execute(f"""
        SELECT {COL_ID_CLIENTE}, {COL_ID_CIDADE} FROM {TABELA_CLIENTES}
        WHERE ID_Endereco IS NULL
    """)
    clientes = cur.fetchall()
    print(f"Gerando endereços para {len(clientes)} clientes...")

    for id_cliente, id_cidade in clientes:
        lat_centro, lon_centro = cache_cidades.get(id_cidade, (None, None))
        lat, lon = jitter_coords(lat_centro, lon_centro, raio_km)

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

        cur.execute(
            f"UPDATE {TABELA_CLIENTES} SET ID_Endereco = ? WHERE {COL_ID_CLIENTE} = ?",
            (id_endereco, id_cliente),
        )

    conn.commit()
    print("Concluído.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    parser.add_argument("--raio-km", type=float, default=5.0,
                         help="raio de dispersão dos endereços ao redor do centro da cidade (padrão: 5km)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)

    print("1) Preparando schema (tabela enderecos + coluna ID_Endereco em clientes)...")
    preparar_schema(conn)

    print("2) Geocodificando cidades distintas (1 request/seg, respeitando o Nominatim)...")
    cache_cidades = geocodificar_cidades(conn)

    print("3) Gerando endereços fictícios por cliente...")
    gerar_enderecos(conn, cache_cidades, args.raio_km)

    conn.close()


if __name__ == "__main__":
    main()
