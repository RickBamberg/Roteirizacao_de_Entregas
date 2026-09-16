"""
Dashboard interativo de roteirização de entregas.

Fluxo:
    1. Escolhe o CD (filtro)
    2. Escolhe o Nível de rota (filtro)
    3. Vê a tabela de lotes disponíveis (CD + Nível de rota + Data_Saida_CD + qtd)
    4. Escolhe um lote (uma linha da tabela) para otimizar
    5. Vê o resultado do VRP: veículos, sequência de paradas, mapa da rota,
       e o comparativo de distância contra os baselines

IMPORTANTE: o cálculo do VRP roda em um PROCESSO SEPARADO
(otimizar_rotas_vrp.py --json, via subprocess), não é importado direto no
processo do Streamlit. Isso evita um problema conhecido no Windows onde o
OR-Tools falha ao carregar sua DLL nativa quando o Streamlit está rodando
(o file watcher do Streamlit interfere no carregamento). O script auxiliar
já sabe devolver o resultado em JSON (--json) só para esse propósito.

Requisitos:
    pip install streamlit plotly pandas --break-system-packages
    (ortools e scikit-learn continuam necessários, mas só no processo
    separado que roda otimizar_rotas_vrp.py)

Uso:
    streamlit run dashboard_rotas.py
"""

import json
import subprocess
import sys
import os
import sqlite3
import math
import urllib.request
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

SCRIPT_VRP = str(Path(__file__).parent / "otimizar_rotas_vrp.py")
OSRM_URL_PADRAO = "http://localhost:5000"

st.set_page_config(page_title="Roteirização de Entregas", layout="wide")
st.title("🚚 Roteirização de Entregas")

# ==============================
# Conexão (só leitura de tabelas, para os filtros e a tabela de lotes —
# o VRP em si roda no subprocess, não usa esta conexão)
# ==============================
db_path = st.sidebar.text_input("Caminho do banco (.db)", value="data/DBDistribuicao.db")

try:
    conn = sqlite3.connect(db_path)
    conn.execute("SELECT 1 FROM centros_distribuicao LIMIT 1")
except Exception as e:
    st.error(f"Não consegui abrir o banco em '{db_path}': {e}")
    st.stop()

# ==============================
# Filtros
# ==============================
cds_disponiveis = pd.read_sql("SELECT DISTINCT UF FROM centros_distribuicao ORDER BY UF", conn)["UF"].tolist()
niveis_disponiveis = ["Dentro da capital", "Região metropolitana", "Interior do estado", "Estado vizinho"]

col_f1, col_f2, col_f3, col_f4 = st.columns(4)
with col_f1:
    cd_escolhido = st.selectbox("Centro de Distribuição", cds_disponiveis)
with col_f2:
    nivel_escolhido = st.selectbox("Nível de rota", niveis_disponiveis)
with col_f3:
    capacidade = st.number_input("Capacidade por veículo", min_value=1, value=25)
with col_f4:
    modo_setor = st.radio("Setores", ["Definir manualmente", "Escolher automaticamente (testa várias opções)"])

if modo_setor == "Definir manualmente":
    n_setores = st.number_input("Nº de setores", min_value=1, value=1)
    max_setores_auto = None
else:
    n_setores = None
    max_setores_auto = st.number_input(
        "Testar de 1 até quantos setores?", min_value=2, max_value=12, value=6,
        help="O dashboard roda o VRP para cada quantidade de setor nessa faixa e usa a que "
             "resultar na menor distância total — mais setores nem sempre é melhor."
    )

# ==============================
# Tabela de lotes disponíveis
# ==============================
query_lotes = """
    SELECT cl.CD_Atribuido, cl.Nivel_Rota, v.Data_Saida_CD, COUNT(*) AS qtd
    FROM vendas v
    JOIN clientes cl ON cl.ID_Cliente = v.ID_Cliente
    WHERE cl.CD_Atribuido = ? AND cl.Nivel_Rota = ?
    GROUP BY v.Data_Saida_CD
    ORDER BY qtd DESC
"""
df_lotes = pd.read_sql(query_lotes, conn, params=(cd_escolhido, nivel_escolhido))
conn.close()

st.subheader(f"Lotes disponíveis — {cd_escolhido} / {nivel_escolhido}")

if df_lotes.empty:
    st.warning("Nenhum lote encontrado para esse CD + nível de rota.")
    st.stop()

st.dataframe(df_lotes, use_container_width=True, height=250)

data_escolhida = st.selectbox(
    "Escolha a Data_Saida_CD para otimizar",
    df_lotes["Data_Saida_CD"].tolist(),
)


def buscar_geometria_rota_osrm(pontos_lat_lon, osrm_url=OSRM_URL_PADRAO):
    """Consulta o endpoint /route do OSRM (diferente do /table, usado só pra
    distância) para obter a geometria real do trajeto — a sequência de
    coordenadas que segue as ruas de verdade, na ordem CD -> paradas -> CD.
    Devolve uma lista de (lat, lon) para desenhar no mapa, ou None se a
    consulta falhar (nesse caso o chamador deve usar a linha reta como
    fallback visual)."""
    coords = ";".join(f"{lon},{lat}" for lat, lon in pontos_lat_lon)
    url = f"{osrm_url}/route/v1/driving/{coords}?geometries=geojson&overview=full"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("code") != "Ok" or not data.get("routes"):
            return None
        # geojson vem como [lon, lat] -> inverte pra (lat, lon), que é o que
        # o Scattermapbox espera
        coords_geo = data["routes"][0]["geometry"]["coordinates"]
        return [(lat, lon) for lon, lat in coords_geo]
    except Exception:
        return None


def calcular_centro_zoom(lats, lons, padding=1.4):
    """Calcula centro e nível de zoom do mapa a partir da dispersão real dos
    pontos — sem isso, rotas pequenas (dentro da capital) ficam ilegíveis
    com o mapa sempre no zoom de país."""
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    lat_center = (lat_min + lat_max) / 2
    lon_center = (lon_min + lon_max) / 2
    lat_range = max(lat_max - lat_min, 0.005) * padding
    lon_range = max(lon_max - lon_min, 0.005) * padding
    max_range = max(lat_range, lon_range)
    zoom = math.log2(360 / max_range) - 1
    zoom = min(14, max(3, zoom))
    return lat_center, lon_center, zoom


def rodar_vrp_subprocess(db_path, cd_uf, data_saida, nivel_rota, capacidade, n_setores):
    """Chama otimizar_rotas_vrp.py --json como processo separado e devolve o dict resultado."""
    cmd = [
        sys.executable, SCRIPT_VRP, db_path,
        "--cd-uf", cd_uf, "--data", data_saida,
        "--capacidade", str(capacidade), "--nivel-rota", nivel_rota,
        "--setores", str(n_setores), "--json",
    ]
    # Força UTF-8 no processo filho — no Windows, o console às vezes usa uma
    # codificação (cp1252) que não suporta acentos, e o print() de nomes de
    # cidade (ex: "Poços de Caldas") pode travar o processo silenciosamente.
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", env=env,
    )
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    if proc.returncode != 0 or not stdout.strip():
        raise RuntimeError(
            f"otimizar_rotas_vrp.py falhou (código de saída {proc.returncode}).\n\n"
            f"--- stdout ---\n{stdout or '(vazio)'}\n\n--- stderr ---\n{stderr or '(vazio)'}"
        )
    try:
        return json.loads(stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as e:
        raise RuntimeError(
            f"Não consegui interpretar a saída do script como JSON.\n\n"
            f"--- stdout ---\n{stdout}\n\n--- stderr ---\n{stderr}"
        ) from e


# ==============================
# Rodar VRP
# ==============================
if st.button("🧭 Otimizar rota deste lote", type="primary"):

    if modo_setor == "Definir manualmente":
        with st.spinner("Resolvendo o problema de roteamento (processo separado)..."):
            try:
                resultado = rodar_vrp_subprocess(
                    db_path, cd_escolhido, data_escolhida, nivel_escolhido, capacidade, n_setores
                )
            except RuntimeError as e:
                st.error(str(e))
                st.stop()
    else:
        # Modo automático: testa 1..max_setores_auto e fica com a de menor distância total
        candidatos = list(range(1, max_setores_auto + 1))
        resultados_por_s = {}
        progresso = st.progress(0.0, text="Testando quantidades de setor...")

        for i, s in enumerate(candidatos):
            try:
                res_s = rodar_vrp_subprocess(
                    db_path, cd_escolhido, data_escolhida, nivel_escolhido, capacidade, s
                )
            except RuntimeError as e:
                st.error(str(e))
                st.stop()
            if not res_s.get("erro"):
                resultados_por_s[s] = res_s
            progresso.progress((i + 1) / len(candidatos), text=f"Testado {s} setor(es)...")

        progresso.empty()

        if not resultados_por_s:
            st.warning("Nenhuma configuração de setor produziu resultado válido.")
            st.stop()

        # ---- Comparativo entre quantidades de setor ----
        st.subheader("🔍 Comparativo — quantidade de setores vs. distância")
        df_comp = pd.DataFrame([
            {
                "Setores": s,
                "Veículos": r["resumo"]["veiculos_total"],
                "Distância otimizada (km)": r["resumo"]["dist_otimizada_km"],
                "Economia (%)": r["resumo"]["economia_pct"],
            }
            for s, r in sorted(resultados_por_s.items())
        ])
        st.dataframe(df_comp, use_container_width=True, hide_index=True)

        melhor_s = min(resultados_por_s, key=lambda s: resultados_por_s[s]["resumo"]["dist_otimizada_km"])
        st.success(f"✅ Melhor opção: **{melhor_s} setor(es)** "
                   f"({resultados_por_s[melhor_s]['resumo']['dist_otimizada_km']:.1f} km otimizados)")

        resultado = resultados_por_s[melhor_s]

    if resultado.get("erro"):
        st.warning(resultado["erro"])
        st.stop()

    resumo = resultado["resumo"]
    deposito = resultado["deposito"]

    # ---- Métricas resumo ----
    st.subheader("📊 Resultado")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Veículos usados", resumo["veiculos_total"])
    m2.metric("Setores", resumo["n_setores"])
    m3.metric("Distância otimizada", f"{resumo['dist_otimizada_km']:.1f} km")
    m4.metric("Economia vs. dedicada", f"{resumo['economia_pct']:.1f}%")

    st.caption(
        f"Ida e volta dedicada: {resumo['dist_dedicada_km']:.1f} km — "
        f"Rota única sem otimizar sequência: {resumo['dist_ordem_original_km']:.1f} km — "
        f"VRP otimizado: {resumo['dist_otimizada_km']:.1f} km"
    )

    # ---- Mapa ----
    st.subheader("🗺️ Mapa das rotas")
    fig = go.Figure()

    lat_cd, lon_cd = deposito["lat"], deposito["lon"]

    # Coleta todos os pontos que vão aparecer no mapa (CD + todas as paradas
    # de todos os setores) para calcular o zoom automaticamente.
    todos_lats, todos_lons = [lat_cd], [lon_cd]
    for setor in resultado["setores"]:
        if setor["erro"]:
            continue
        for r in setor["rotas"]:
            for p in r["paradas"]:
                todos_lats.append(p["lat"])
                todos_lons.append(p["lon"])
    centro_lat, centro_lon, zoom = calcular_centro_zoom(todos_lats, todos_lons)

    fig.add_trace(go.Scattermapbox(
        lat=[lat_cd], lon=[lon_cd], mode="markers",
        marker=dict(size=16, color="black", symbol="circle"),
        name="CD", text=["Centro de Distribuição"],
    ))

    cores = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
             "#46f0f0", "#f032e6", "#bcf60c", "#fabebe", "#008080"]

    cor_idx = 0
    for setor in resultado["setores"]:
        if setor["erro"]:
            st.warning(f"Setor '{setor['nome']}': {setor['erro']}")
            continue
        usou_osrm_setor = setor.get("usou_osrm", False)
        for r in setor["rotas"]:
            paradas_lat_lon = [(p["lat"], p["lon"]) for p in r["paradas"]]
            pontos_ordem = [(lat_cd, lon_cd)] + paradas_lat_lon + [(lat_cd, lon_cd)]

            geometria = None
            if usou_osrm_setor:
                geometria = buscar_geometria_rota_osrm(pontos_ordem)

            if geometria:
                # segue as ruas de verdade
                lats = [pt[0] for pt in geometria]
                lons = [pt[1] for pt in geometria]
                # texto só nos marcadores de parada, não em cada ponto da geometria
                textos = None
            else:
                # fallback: linha reta entre as paradas (comportamento original —
                # usado quando o lote é 'Estado vizinho'/haversine, ou se a
                # consulta ao /route falhar por qualquer motivo)
                lats = [lat_cd] + [p["lat"] for p in r["paradas"]] + [lat_cd]
                lons = [lon_cd] + [p["lon"] for p in r["paradas"]] + [lon_cd]
                textos = ["CD"] + [f"{p['pedido']} — {p['cidade']}/{p['uf']} — {p['logradouro']}, {p['numero']}"
                                    for p in r["paradas"]] + ["CD"]

            cor = cores[cor_idx % len(cores)]
            cor_idx += 1

            fig.add_trace(go.Scattermapbox(
                lat=lats, lon=lons, mode="lines" if geometria else "lines+markers",
                line=dict(width=3 if geometria else 2, color=cor),
                marker=dict(size=8, color=cor) if not geometria else None,
                name=f"{setor['nome']} - Veículo {r['veiculo']} ({r['distancia_km']:.0f} km)",
                text=textos, hoverinfo="text" if textos else "skip",
            ))

            if geometria:
                # adiciona marcadores das paradas por cima da linha de rota
                # (a linha em si não carrega texto por parada nesse modo)
                fig.add_trace(go.Scattermapbox(
                    lat=[p["lat"] for p in r["paradas"]], lon=[p["lon"] for p in r["paradas"]],
                    mode="markers", marker=dict(size=8, color=cor),
                    text=[f"{p['pedido']} — {p['cidade']}/{p['uf']} — {p['logradouro']}, {p['numero']}"
                          for p in r["paradas"]],
                    hoverinfo="text", showlegend=False,
                ))

    fig.update_layout(
        mapbox=dict(style="open-street-map", center=dict(lat=centro_lat, lon=centro_lon), zoom=zoom),
        margin=dict(l=0, r=0, t=0, b=0), height=550,
        legend=dict(orientation="h", yanchor="bottom", y=-0.15),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ---- Detalhe por setor/veículo ----
    st.subheader("📋 Sequência de paradas")
    for setor in resultado["setores"]:
        if setor["erro"]:
            continue
        for r in setor["rotas"]:
            with st.expander(f"{setor['nome']} — Veículo {r['veiculo']} ({len(r['paradas'])} paradas, {r['distancia_km']:.1f} km)"):
                df_seq = pd.DataFrame(
                    [
                        (pos, p["pedido"], p["cidade"], p["uf"],
                         f"{p['logradouro']}, {p['numero']} — {p['bairro']}" if p.get("logradouro") else "")
                        for pos, p in enumerate(r["paradas"], start=1)
                    ],
                    columns=["Ordem", "Pedido", "Cidade", "UF", "Endereço"],
                )
                st.dataframe(df_seq, use_container_width=True, hide_index=True)
