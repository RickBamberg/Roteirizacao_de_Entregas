"""
Corrige endereços da Bahia cuja coordenada caiu fora da malha viária
(provavelmente n'água) — identificados por diagnosticar_enderecos_bahia.py.

Para cada endereço suspeito, sorteia um novo ponto em volta do centro da
cidade (raio crescente) e valida contra o /nearest do OSRM antes de aceitar
— só grava a nova coordenada se ela estiver a uma distância aceitável de
alguma rua real. Se não conseguir achar um ponto válido depois de várias
tentativas, usa como último recurso o próprio ponto de rua mais próximo do
centro da cidade (sempre válido, só perde um pouco da dispersão aleatória).

Requisitos: servidor OSRM rodando (osrm-routed) na porta 5000, cobrindo a
Bahia (mesmo grafo usado pelos outros scripts).

Uso:
    # modo seguro: só mostra o que faria, não grava nada
    python corrigir_enderecos_na_agua.py data/DBDistribuicao.db --csv enderecos_suspeitos_bahia.csv --dry-run

    # aplica de verdade
    python corrigir_enderecos_na_agua.py data/DBDistribuicao.db --csv enderecos_suspeitos_bahia.csv
"""

import sqlite3
import argparse
import json
import csv
import random
import sys
import urllib.request
import urllib.error
from math import cos, radians

OSRM_URL_PADRAO = "http://localhost:5000"


def consultar_nearest(lat, lon, osrm_url):
    """Retorna (distancia_m, lat_snapado, lon_snapado) do ponto de rua mais
    próximo, ou None se a consulta falhar."""
    url = f"{osrm_url}/nearest/v1/driving/{lon},{lat}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("code") != "Ok" or not data.get("waypoints"):
            return None
        wp = data["waypoints"][0]
        lon_snap, lat_snap = wp["location"]
        return wp["distance"], lat_snap, lon_snap
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def ponto_aleatorio_perto(lat_centro, lon_centro, raio_km):
    """Sorteia um ponto uniformemente dentro de um círculo de raio_km ao
    redor do centro (aproximação simples, suficiente pra essa escala)."""
    import math
    raio_deg_lat = raio_km / 111.0  # ~111km por grau de latitude
    raio_deg_lon = raio_km / (111.0 * cos(radians(lat_centro)) or 1)
    ang = random.uniform(0, 2 * math.pi)
    r = random.uniform(0, 1) ** 0.5  # distribuição uniforme na área do círculo
    dlat = raio_deg_lat * r * math.cos(ang)
    dlon = raio_deg_lon * r * math.sin(ang)
    return lat_centro + dlat, lon_centro + dlon


def buscar_ponto_valido(lat_centro, lon_centro, limiar_m, raio_max_km, tentativas, osrm_url):
    """Tenta achar um ponto perto do centro da cidade que fique perto o
    suficiente de uma rua real. Raio cresce a cada leva de tentativas.
    Retorna (lat, lon, distancia_m, era_fallback)."""
    raios = [0.5, 1.0, 2.0, raio_max_km]
    for raio_km in raios:
        for _ in range(tentativas):
            lat, lon = ponto_aleatorio_perto(lat_centro, lon_centro, raio_km)
            resultado = consultar_nearest(lat, lon, osrm_url)
            if resultado is None:
                continue
            distancia_m, _, _ = resultado
            if distancia_m <= limiar_m:
                return lat, lon, distancia_m, False

    # fallback: usa o próprio ponto de rua mais próximo do centro da cidade
    resultado = consultar_nearest(lat_centro, lon_centro, osrm_url)
    if resultado is not None:
        distancia_m, lat_snap, lon_snap = resultado
        return lat_snap, lon_snap, distancia_m, True

    return None


def carregar_suspeitos_do_csv(caminho_csv):
    with open(caminho_csv, encoding="utf-8-sig") as f:
        return [int(row["ID_Endereco"]) for row in csv.DictReader(f)]


def buscar_cidade_do_endereco(conn, id_endereco):
    cur = conn.cursor()
    cur.execute("""
        SELECT ci.Cidade, ci.UF, ci.Latitude, ci.Longitude
        FROM enderecos e
        JOIN clientes cl ON cl.ID_Endereco = e.ID_Endereco
        JOIN cidades ci ON ci.ID_Cidade = cl.ID_Cidade
        WHERE e.ID_Endereco = ?
        LIMIT 1
    """, (id_endereco,))
    return cur.fetchone()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    parser.add_argument("--csv", required=True,
                         help="CSV gerado por diagnosticar_enderecos_bahia.py, com a coluna ID_Endereco")
    parser.add_argument("--limiar-km", type=float, default=0.1,
                         help="distância máxima aceitável até a rua mais próxima para o NOVO ponto (padrão: 0.1 — "
                              "mais apertado que o teste de detecção, para não deixar pontos perto de enseadas estreitas)")
    parser.add_argument("--raio-max-km", type=float, default=4.0,
                         help="raio máximo de busca em volta do centro da cidade (padrão: 4.0)")
    parser.add_argument("--tentativas-por-raio", type=int, default=15)
    parser.add_argument("--osrm-url", default=OSRM_URL_PADRAO)
    parser.add_argument("--dry-run", action="store_true",
                         help="mostra o que seria corrigido, sem gravar no banco")
    parser.add_argument("--cidades", default=None,
                         help="se informado, só corrige endereços dessas cidades (separadas por vírgula, ex: "
                              "'Salvador,Ilhéus,Cachoeira'). Útil para rodar um limiar apertado só em cidades "
                              "litorâneas/ribeirinhas, sem mexer em endereços rurais do interior que só estão "
                              "longe de rua por causa de cobertura esparsa do OSM, não por estarem na água.")
    parser.add_argument("--seed", type=int, default=42, help="semente aleatória, para resultado reprodutível")
    args = parser.parse_args()

    random.seed(args.seed)
    limiar_m = args.limiar_km * 1000

    ids_suspeitos = carregar_suspeitos_do_csv(args.csv)
    cidades_filtro = None
    if args.cidades:
        cidades_filtro = {c.strip().lower() for c in args.cidades.split(",")}

    print(f"{len(ids_suspeitos)} endereços lidos de {args.csv}.")

    conn = sqlite3.connect(args.db_path)
    corrigidos, fallbacks, falhas, ignorados = 0, 0, [], 0

    for id_endereco in ids_suspeitos:
        cidade_info = buscar_cidade_do_endereco(conn, id_endereco)
        if cidade_info is None:
            falhas.append((id_endereco, "não achei a cidade desse endereço"))
            continue
        cidade, uf, lat_centro, lon_centro = cidade_info

        if cidades_filtro is not None and cidade.strip().lower() not in cidades_filtro:
            ignorados += 1
            continue

        resultado = buscar_ponto_valido(
            lat_centro, lon_centro, limiar_m, args.raio_max_km, args.tentativas_por_raio, args.osrm_url
        )
        if resultado is None:
            falhas.append((id_endereco, f"OSRM não respondeu para {cidade}/{uf}"))
            continue

        lat_novo, lon_novo, distancia_m, era_fallback = resultado
        tag = "[fallback: centro da cidade]" if era_fallback else ""
        print(f"  ID_Endereco={id_endereco} ({cidade}/{uf}): novo ponto a {distancia_m:.0f}m da rua {tag}")

        if era_fallback:
            fallbacks += 1

        if not args.dry_run:
            conn.execute(
                "UPDATE enderecos SET Latitude = ?, Longitude = ? WHERE ID_Endereco = ?",
                (lat_novo, lon_novo, id_endereco),
            )
        corrigidos += 1

    if not args.dry_run:
        conn.commit()
    conn.close()

    print(f"\nCorrigidos: {corrigidos} ({fallbacks} usando fallback do centro da cidade)")
    if cidades_filtro is not None:
        print(f"Ignorados (fora do filtro --cidades): {ignorados}")
    if falhas:
        print(f"Falharam: {len(falhas)}")
        for id_endereco, motivo in falhas:
            print(f"  ID_Endereco={id_endereco}: {motivo}")
    if args.dry_run:
        print("\n[DRY RUN] Nada foi gravado no banco. Rode sem --dry-run para aplicar de verdade.")


if __name__ == "__main__":
    main()
