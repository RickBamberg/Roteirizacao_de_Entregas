"""
Diagnóstico: identifica endereços da Bahia cuja coordenada (lat/lon) cai
fora da malha viária coberta pelo OSRM — bom indício de que o ponto foi
gerado dentro d'água (baía, oceano) em vez de em terra.

Usa o endpoint /nearest do OSRM: para cada endereço, pergunta "qual a rua
mais próxima e a que distância?". Se a distância até a rua mais próxima for
grande (ex: > 1 km), é sinal de que o ponto está isolado — em terra, quase
sempre existe alguma rua nas proximidades, mesmo que pequena.

Requisitos: servidor OSRM rodando (osrm-routed) na porta 5000, cobrindo a
Bahia (mesmo grafo usado por otimizar_rotas_vrp.py).

Uso:
    python diagnosticar_enderecos_bahia.py data/DBDistribuicao.db
    python diagnosticar_enderecos_bahia.py data/DBDistribuicao.db --limiar-km 2 --csv suspeitos.csv
"""

import sqlite3
import argparse
import json
import sys
import time
import urllib.request
import urllib.error

OSRM_URL_PADRAO = "http://localhost:5000"


def consultar_nearest(lat, lon, osrm_url):
    """Pergunta ao OSRM qual o ponto de rua mais próximo dessa coordenada e
    a que distância (em metros). Retorna None se a consulta falhar."""
    url = f"{osrm_url}/nearest/v1/driving/{lon},{lat}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("code") != "Ok" or not data.get("waypoints"):
            return None
        return data["waypoints"][0]["distance"]  # metros
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def carregar_enderecos_bahia(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT e.ID_Endereco, e.Latitude, e.Longitude,
               ci.Cidade, ci.UF, e.Logradouro, e.Numero
        FROM enderecos e
        JOIN clientes cl ON cl.ID_Endereco = e.ID_Endereco
        JOIN cidades ci ON ci.ID_Cidade = cl.ID_Cidade
        WHERE cl.CD_Atribuido = 'BA'
          AND cl.Nivel_Rota IN ('Dentro da capital', 'Região metropolitana', 'Interior do estado')
    """)
    linhas = cur.fetchall()
    conn.close()
    return linhas


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    parser.add_argument("--limiar-km", type=float, default=1.0,
                         help="distância mínima até a rua mais próxima (em km) para considerar suspeito (padrão: 1.0)")
    parser.add_argument("--osrm-url", default=OSRM_URL_PADRAO)
    parser.add_argument("--csv", default=None,
                         help="se informado, salva os endereços suspeitos nesse arquivo CSV")
    parser.add_argument("--limite", type=int, default=None,
                         help="testa só os N primeiros endereços (útil para uma checagem rápida antes de rodar tudo)")
    args = parser.parse_args()

    enderecos = carregar_enderecos_bahia(args.db_path)
    if args.limite:
        enderecos = enderecos[:args.limite]

    print(f"Verificando {len(enderecos)} endereços da Bahia contra a malha viária do OSRM...")

    suspeitos = []
    falhas_consulta = 0
    limiar_m = args.limiar_km * 1000

    for i, (id_end, lat, lon, cidade, uf, logradouro, numero) in enumerate(enderecos, start=1):
        distancia_m = consultar_nearest(lat, lon, args.osrm_url)
        if distancia_m is None:
            falhas_consulta += 1
        elif distancia_m > limiar_m:
            suspeitos.append((id_end, lat, lon, cidade, uf, logradouro, numero, distancia_m))

        if i % 500 == 0:
            print(f"  ... {i}/{len(enderecos)} verificados ({len(suspeitos)} suspeitos até agora)")

    print(f"\nTotal verificado: {len(enderecos)}")
    print(f"Consultas que falharam (servidor OSRM indisponível?): {falhas_consulta}")
    print(f"Endereços suspeitos (> {args.limiar_km} km da rua mais próxima): {len(suspeitos)}")

    if suspeitos:
        print("\nExemplos (até 20):")
        for id_end, lat, lon, cidade, uf, logradouro, numero, dist_m in suspeitos[:20]:
            print(f"  ID_Endereco={id_end} | {cidade}/{uf} | {logradouro}, {numero} "
                  f"| lat={lat:.5f}, lon={lon:.5f} | {dist_m/1000:.2f} km até a rua mais próxima")

    if args.csv and suspeitos:
        import csv
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["ID_Endereco", "Latitude", "Longitude", "Cidade", "UF",
                              "Logradouro", "Numero", "Distancia_m"])
            writer.writerows(suspeitos)
        print(f"\nLista completa salva em: {args.csv}")


if __name__ == "__main__":
    main()
