"""
Resolve o problema de roteamento de veículos (CVRP - Capacitated Vehicle
Routing Problem) para um lote de pedidos que sai do mesmo CD na mesma data
(a Data_Saida_CD calculada pela simulação de despacho em lote).

Usa OR-Tools (routing solver do Google) para definir:
    - quantos veículos usar
    - quais paradas cada veículo atende
    - em que ordem (sequência que minimiza distância total)

Compara contra 2 baselines "ingênuos" para mostrar o ganho da otimização:
    1. Ida e volta dedicada por pedido (pior caso, sem consolidar carga)
    2. Rota única na ordem em que os pedidos aparecem no banco (sem otimizar sequência)

Requisitos:
    pip install ortools --break-system-packages

Uso:
    python otimizar_rotas_vrp.py caminho_do_banco.db --cd-uf BA --data 2020-06-15 --capacidade 25
"""

import sqlite3
import argparse
import json
import sys
import io
import urllib.request
import urllib.error
from math import cos, radians, sin, asin, sqrt

# Garante UTF-8 na saída padrão mesmo em consoles Windows com codepage
# diferente (evita travar ao imprimir nomes de cidade acentuados).
if sys.stdout.encoding is None or sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from ortools.constraint_solver import routing_enums_pb2, pywrapcp
from sklearn.cluster import KMeans

TABELA_CLIENTES = "clientes"
TABELA_CIDADES = "cidades"
TABELA_ENDERECOS = "enderecos"
TABELA_CD = "centros_distribuicao"
TABELA_VENDAS = "vendas"

OSRM_URL_PADRAO = "http://localhost:5000"

# Mesma caixa delimitadora usada no `osmium extract` que gerou bahia.osm.pbf
# (min_lon, min_lat, max_lon, max_lat). Usada para detectar pontos fora da
# área coberta pelo grafo OSRM antes de consultar a API — o OSRM não avisa
# sozinho quando um ponto está fora do mapa carregado, ele só "gruda" no nó
# de rua mais próximo que encontrar, o que pode gerar distância errada em
# silêncio em vez de um erro explícito.
BBOX_OSRM_CARREGADO = (-46.62, -18.35, -37.34, -8.53)


def ponto_dentro_bbox(lat, lon, bbox=BBOX_OSRM_CARREGADO):
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


def carregar_lote(conn, cd_uf, data_saida, nivel_rota=None):
    cur = conn.cursor()
    cur.execute(f"SELECT Latitude, Longitude FROM {TABELA_CD} WHERE UF = ?", (cd_uf,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"CD não encontrado para UF={cd_uf}")
    lat_cd, lon_cd = row

    query = f"""
        SELECT v.ID_Pedido, e.Latitude, e.Longitude, ci.Cidade, ci.UF,
               e.Logradouro, e.Numero, e.Bairro
        FROM {TABELA_VENDAS} v
        JOIN {TABELA_CLIENTES} cl ON cl.ID_Cliente = v.ID_Cliente
        JOIN {TABELA_ENDERECOS} e ON e.ID_Endereco = cl.ID_Endereco
        JOIN {TABELA_CIDADES} ci ON ci.ID_Cidade = cl.ID_Cidade
        WHERE cl.CD_Atribuido = ? AND v.Data_Saida_CD = ?
    """
    params = [cd_uf, data_saida]
    if nivel_rota:
        query += " AND cl.Nivel_Rota = ?"
        params.append(nivel_rota)

    cur.execute(query, params)
    pedidos = cur.fetchall()  # (ID_Pedido, lat, lon, Cidade, UF, Logradouro, Numero, Bairro)

    return (lat_cd, lon_cd), pedidos


def setorizar(deposito, pedidos, n_setores):
    """Agrupa os pedidos em n_setores por proximidade geográfica (KMeans).
    Rotula cada setor com um número sequencial (Setor 1, 2, 3...) — mais
    genérico que direção cardinal, que só faz sentido para entrega local
    dentro de uma cidade e colide com frequência em rotas de longa distância
    (estado vizinho), onde os setores representam comboios/destinos
    diferentes, não quadrantes de uma mesma cidade."""
    if n_setores <= 1 or len(pedidos) <= n_setores:
        return {"Setor único": pedidos}

    coords = [(lat, lon) for _, lat, lon, *_ in pedidos]
    km = KMeans(n_clusters=n_setores, random_state=42, n_init=10)
    labels = km.fit_predict(coords)

    lat_cd, lon_cd = deposito
    # ordena os setores pela direção predominante (ângulo em relação ao CD),
    # só para a numeração sair num sentido visualmente coerente (não aleatória)
    centros = []
    for cluster_id in range(n_setores):
        pontos_cluster = [pedidos[i] for i in range(len(pedidos)) if labels[i] == cluster_id]
        if not pontos_cluster:
            continue
        centro_lat = sum(p[1] for p in pontos_cluster) / len(pontos_cluster)
        centro_lon = sum(p[2] for p in pontos_cluster) / len(pontos_cluster)
        angulo = _angulo_em_relacao_ao_cd(lat_cd, lon_cd, centro_lat, centro_lon)
        centros.append((angulo, cluster_id, pontos_cluster))
    centros.sort(key=lambda x: x[0])

    setores = {}
    for i, (_, cluster_id, pontos_cluster) in enumerate(centros, start=1):
        setores[f"Setor {i}"] = pontos_cluster

    return setores


def _angulo_em_relacao_ao_cd(lat_cd, lon_cd, lat_ponto, lon_ponto):
    from math import atan2, degrees
    return degrees(atan2(lon_ponto - lon_cd, lat_ponto - lat_cd)) % 360


def matriz_via_osrm(pontos, osrm_url):
    """Consulta o endpoint /table do OSRM para obter a matriz de distância
    real de rodovia (em km) entre todos os pontos. Levanta exceção se o
    servidor não responder, não cobrir a área, ou devolver erro."""
    coords = ";".join(f"{lon},{lat}" for lat, lon in pontos)
    url = f"{osrm_url}/table/v1/driving/{coords}?annotations=distance"
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM retornou código '{data.get('code')}' (fora da área coberta pelo extrato?)")
    # distances vem em metros -> converte pra km. None indica par sem rota
    # encontrada (ex: ilha sem conexão) -> tratado como 0 (evita crash;
    # aparece como aviso pra investigar se acontecer com frequência).
    matriz = []
    for linha in data["distances"]:
        matriz.append([(d / 1000) if d is not None else 0.0 for d in linha])
    return matriz


def montar_matriz_distancia(deposito, pedidos, usar_osrm=True, osrm_url=OSRM_URL_PADRAO):
    pontos = [deposito] + [(lat, lon) for _, lat, lon, *_ in pedidos]

    if usar_osrm:
        fora_da_area = [p for p in pontos if not ponto_dentro_bbox(*p)]
        if fora_da_area:
            print(f"[info] {len(fora_da_area)} ponto(s) fora da área coberta pelo OSRM (Bahia) — "
                  f"lote provavelmente é 'Estado vizinho'. Usando distância em linha reta (haversine) "
                  f"para o lote inteiro, para não misturar rota real com rota snapada incorretamente.",
                  file=sys.stderr)
        else:
            try:
                return matriz_via_osrm(pontos, osrm_url), pontos, True
            except (urllib.error.URLError, RuntimeError, TimeoutError, OSError) as e:
                print(f"[aviso] OSRM indisponível ({e}) — usando distância em linha reta (haversine) como fallback.",
                      file=sys.stderr)

    n = len(pontos)
    matriz = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                matriz[i][j] = haversine_km(*pontos[i], *pontos[j])
    return matriz, pontos, False


def resolver_vrp(matriz, n_pedidos, capacidade):
    n_veiculos = max(1, -(-n_pedidos // capacidade))  # ceil(n_pedidos / capacidade)

    manager = pywrapcp.RoutingIndexManager(len(matriz), n_veiculos, 0)  # nó 0 = depósito
    routing = pywrapcp.RoutingModel(manager)

    def distancia_callback(from_idx, to_idx):
        i, j = manager.IndexToNode(from_idx), manager.IndexToNode(to_idx)
        return int(matriz[i][j] * 1000)  # metros, inteiro (OR-Tools trabalha com int)

    transit_idx = routing.RegisterTransitCallback(distancia_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)

    demandas = [0] + [1] * n_pedidos  # cada pedido consome 1 unidade de capacidade

    def demanda_callback(from_idx):
        return demandas[manager.IndexToNode(from_idx)]

    demanda_idx = routing.RegisterUnaryTransitCallback(demanda_callback)
    routing.AddDimensionWithVehicleCapacity(
        demanda_idx, 0, [capacidade] * n_veiculos, True, "Capacidade"
    )

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(10)

    solucao = routing.SolveWithParameters(params)
    return manager, routing, solucao, n_veiculos


def extrair_rotas(manager, routing, solucao, n_veiculos, pedidos):
    rotas = []
    distancia_total_km = 0.0
    for veiculo in range(n_veiculos):
        idx = routing.Start(veiculo)
        sequencia = []
        dist_veiculo_m = 0
        while not routing.IsEnd(idx):
            no = manager.IndexToNode(idx)
            if no != 0:
                id_pedido, lat, lon, cidade, uf, logradouro, numero, bairro = pedidos[no - 1]
                sequencia.append((id_pedido, cidade, uf, lat, lon, logradouro, numero, bairro))
            prox_idx = solucao.Value(routing.NextVar(idx))
            dist_veiculo_m += routing.GetArcCostForVehicle(idx, prox_idx, veiculo)
            idx = prox_idx
        if sequencia:
            rotas.append({"veiculo": veiculo + 1, "pedidos": sequencia, "distancia_km": dist_veiculo_m / 1000})
            distancia_total_km += dist_veiculo_m / 1000
    return rotas, distancia_total_km


def baseline_ida_volta_dedicada(matriz, n_pedidos):
    return sum(2 * matriz[0][i + 1] for i in range(n_pedidos))


def baseline_ordem_original(matriz, n_pedidos):
    total = 0.0
    atual = 0
    for i in range(1, n_pedidos + 1):
        total += matriz[atual][i]
        atual = i
    total += matriz[atual][0]
    return total


def resolver_lote(db_path, cd_uf, data_saida, nivel_rota, capacidade, n_setores,
                   usar_osrm=True, osrm_url=OSRM_URL_PADRAO):
    """Roda o pipeline completo (carregar -> setorizar -> VRP por setor) e
    devolve um dicionário estruturado com tudo que a UI precisa — usado tanto
    pelo modo texto (main) quanto pelo modo --json (para o dashboard)."""
    conn = sqlite3.connect(db_path)
    deposito, pedidos = carregar_lote(conn, cd_uf, data_saida, nivel_rota)
    conn.close()

    resultado = {
        "cd_uf": cd_uf, "nivel_rota": nivel_rota, "data_saida": data_saida,
        "n_pedidos": len(pedidos),
        "deposito": {"lat": deposito[0], "lon": deposito[1]},
        "setores": [],
        "resumo": None,
        "erro": None,
    }

    if not pedidos:
        resultado["erro"] = "Nenhum pedido encontrado para esse CD/data/nível de rota."
        return resultado

    setores = setorizar(deposito, pedidos, n_setores)

    dist_otimizada_total = 0.0
    dist_dedicada_total = 0.0
    dist_ordem_original_total = 0.0
    veiculos_total = 0

    for nome_setor, pedidos_setor in setores.items():
        matriz, _, usou_osrm_setor = montar_matriz_distancia(deposito, pedidos_setor, usar_osrm=usar_osrm, osrm_url=osrm_url)
        n_ped_setor = len(pedidos_setor)

        manager, routing, solucao, n_veiculos = resolver_vrp(matriz, n_ped_setor, capacidade)
        if solucao is None:
            resultado["setores"].append({"nome": nome_setor, "erro": "sem solução", "rotas": [], "usou_osrm": False})
            continue

        rotas, dist_otimizada = extrair_rotas(manager, routing, solucao, n_veiculos, pedidos_setor)
        dist_dedicada = baseline_ida_volta_dedicada(matriz, n_ped_setor)
        dist_ordem_original = baseline_ordem_original(matriz, n_ped_setor)

        rotas_json = []
        for r in rotas:
            rotas_json.append({
                "veiculo": r["veiculo"],
                "distancia_km": round(r["distancia_km"], 2),
                "paradas": [
                    {"pedido": p[0], "cidade": p[1], "uf": p[2], "lat": p[3], "lon": p[4],
                     "logradouro": p[5], "numero": p[6], "bairro": p[7]}
                    for p in r["pedidos"]
                ],
            })

        resultado["setores"].append({"nome": nome_setor, "erro": None, "rotas": rotas_json, "usou_osrm": usou_osrm_setor})

        dist_otimizada_total += dist_otimizada
        dist_dedicada_total += dist_dedicada
        dist_ordem_original_total += dist_ordem_original
        veiculos_total += len(rotas)

    economia = (1 - dist_otimizada_total / dist_dedicada_total) * 100 if dist_dedicada_total > 0 else 0
    resultado["resumo"] = {
        "dist_dedicada_km": round(dist_dedicada_total, 2),
        "dist_ordem_original_km": round(dist_ordem_original_total, 2),
        "dist_otimizada_km": round(dist_otimizada_total, 2),
        "veiculos_total": veiculos_total,
        "n_setores": len(setores),
        "economia_pct": round(economia, 2),
    }
    return resultado


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    parser.add_argument("--cd-uf", required=True)
    parser.add_argument("--data", required=True, help="Data_Saida_CD, formato YYYY-MM-DD")
    parser.add_argument("--capacidade", type=int, default=25, help="pedidos por veículo (padrão 25)")
    parser.add_argument("--nivel-rota", default=None,
                         help="filtra só um nível de rota (ex: 'Dentro da capital', 'Estado vizinho'). "
                              "Recomendado sempre informar — misturar níveis no mesmo VRP não reflete a operação real.")
    parser.add_argument("--setores", type=int, default=1,
                         help="divide o lote em N setores geográficos (ex: 4) antes de rodar o VRP em cada um. "
                              "Recomendado para cidades grandes / lotes com muitos pedidos espalhados (padrão: 1, sem setorizar).")
    parser.add_argument("--json", action="store_true",
                         help="imprime o resultado em JSON (uma linha) em vez de texto legível — usado pelo dashboard")
    parser.add_argument("--osrm-url", default=OSRM_URL_PADRAO,
                         help=f"URL do servidor OSRM local (padrão: {OSRM_URL_PADRAO})")
    parser.add_argument("--sem-osrm", action="store_true",
                         help="ignora o OSRM e usa direto a distância em linha reta (haversine)")
    args = parser.parse_args()

    resultado = resolver_lote(
        args.db_path, args.cd_uf.upper(), args.data, args.nivel_rota, args.capacidade, args.setores,
        usar_osrm=not args.sem_osrm, osrm_url=args.osrm_url,
    )

    if args.json:
        print(json.dumps(resultado, ensure_ascii=False))
        return

    print(f"Lote: CD={resultado['cd_uf']}, Nivel_Rota={resultado['nivel_rota'] or 'TODOS (não recomendado)'}, "
          f"Data_Saida_CD={resultado['data_saida']} -> {resultado['n_pedidos']} pedidos")
    if resultado["erro"]:
        print(resultado["erro"])
        return

    for setor in resultado["setores"]:
        print(f"\n=== Setor: {setor['nome']} ===")
        if setor["erro"]:
            print(f"  {setor['erro']}")
            continue
        for r in setor["rotas"]:
            print(f"  Veículo {r['veiculo']}: {len(r['paradas'])} paradas, {r['distancia_km']:.1f} km")
            for pos, p in enumerate(r["paradas"], start=1):
                endereco = f"{p['logradouro']}, {p['numero']}" if p.get('logradouro') else ""
                print(f"      {pos}. Pedido {p['pedido']} — {p['cidade']}/{p['uf']}"
                      + (f" — {endereco}" if endereco else ""))

    resumo = resultado["resumo"]
    print(f"\n📊 Comparativo de distância total (todos os setores somados):")
    print(f"  Ida e volta dedicada (sem consolidar carga): {resumo['dist_dedicada_km']:.1f} km")
    print(f"  Rota única na ordem original (sem otimizar sequência): {resumo['dist_ordem_original_km']:.1f} km")
    print(f"  VRP otimizado ({resumo['veiculos_total']} veículos, {resumo['n_setores']} setores): "
          f"{resumo['dist_otimizada_km']:.1f} km")
    print(f"  Economia vs. ida e volta dedicada: {resumo['economia_pct']:.1f}%")


if __name__ == "__main__":
    main()
