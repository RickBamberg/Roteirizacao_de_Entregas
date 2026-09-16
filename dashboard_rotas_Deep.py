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
    pip install streamlit plotly pandas requests polyline --break-system-packages
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
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

SCRIPT_VRP = str(Path(__file__).parent / "otimizar_rotas_vrp.py")

st.set_page_config(page_title="Roteirização de Entregas", layout="wide")
st.title("🚚 Roteirização de Entregas")

# ==============================
# Estilos CSS
# ==============================
st.markdown("""
<style>
    .stProgress > div > div {
        background-color: #4CAF50 !important;
    }
    .stProgress > div > div > div {
        background: linear-gradient(90deg, #4CAF50, #45a049) !important;
    }
</style>
""", unsafe_allow_html=True)

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

# Filtros em 5 colunas
col_f1, col_f2, col_f3, col_f4, col_f5 = st.columns(5)

with col_f1:
    cd_escolhido = st.selectbox("Centro de Distribuição", cds_disponiveis)

with col_f2:
    nivel_escolhido = st.selectbox("Nível de rota", niveis_disponiveis)

with col_f3:
    capacidade = st.number_input("Capacidade por veículo", min_value=1, value=25)

with col_f4:
    use_real_routes = st.checkbox(
        "🌐 Rotas reais", 
        value=False,
        help="Usa OSRM (OpenStreetMap) para rotas reais. "
             "Mais lento, mas preciso. Desative para testes rápidos."
    )
    if use_real_routes:
        st.caption("⚠️ Pode demorar alguns segundos")

with col_f5:
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


def rodar_vrp_subprocess(db_path, cd_uf, data_saida, nivel_rota, capacidade, n_setores, use_real_routes=False):
    """Chama otimizar_rotas_vrp.py --json como processo separado e devolve o dict resultado."""
    cmd = [
        sys.executable, SCRIPT_VRP, db_path,
        "--cd-uf", cd_uf, "--data", data_saida,
        "--capacidade", str(capacidade), "--nivel-rota", nivel_rota,
        "--setores", str(n_setores), "--json"
    ]
    
    # Adiciona a flag se usar rotas reais
    if use_real_routes:
        cmd.append("--use-real-routes")
    
    # Remove argumentos vazios
    cmd = [arg for arg in cmd if arg]
    
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
        return json.loads(stdout.strip())
    except json.JSONDecodeError as e:
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
                    db_path, cd_escolhido, data_escolhida, nivel_escolhido, 
                    capacidade, n_setores, use_real_routes
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
                    db_path, cd_escolhido, data_escolhida, nivel_escolhido, 
                    capacidade, s, use_real_routes
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

    # ---- Verifica se o resultado tem erro ----
    if resultado.get("erro"):
        st.warning(resultado["erro"])
        st.stop()

    # ---- Extrai dados do resultado ----
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
    
    # ==========================================
    # 1. COLETA APENAS COORDENADAS VÁLIDAS
    # ==========================================
    # Só aceita coordenadas dentro do Brasil
    def is_valid_br_coord(lat, lon):
        return -33 < lat < 5 and -74 < lon < -34
    
    # Lista para coordenadas válidas
    coords_validas = []
    
    # Adiciona o CD se for válido
    if is_valid_br_coord(lat_cd, lon_cd):
        coords_validas.append((lat_cd, lon_cd))
    
    # Coleta paradas válidas
    for setor in resultado["setores"]:
        if setor["erro"]:
            continue
        for r in setor["rotas"]:
            for p in r["paradas"]:
                lat = p.get("lat", 0)
                lon = p.get("lon", 0)
                if is_valid_br_coord(lat, lon):
                    coords_validas.append((lat, lon))
    
    # ==========================================
    # 2. SE NÃO TEM COORDENADAS VÁLIDAS, USA FALLBACK
    # ==========================================
    if not coords_validas:
        st.error("❌ Nenhuma coordenada válida encontrada!")
        st.stop()
    
    # ==========================================
    # 3. CALCULA CENTRO E ZOOM DIRETO
    # ==========================================
    lats = [lat for lat, lon in coords_validas]
    lons = [lon for lat, lon in coords_validas]
    
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    
    # Centro
    centro_lat = (lat_min + lat_max) / 2
    centro_lon = (lon_min + lon_max) / 2
    
    # Calcula a dispersão
    lat_range = lat_max - lat_min
    lon_range = lon_max - lon_min
    
    # Se todos os pontos estão muito próximos, usa um zoom fixo de rua
    if lat_range < 0.01 and lon_range < 0.01:
        zoom = 16  # Zoom de rua (mais próximo)
    else:
        # Calcula o zoom baseado na dispersão
        max_range = max(lat_range, lon_range, 0.01)
        zoom = math.log2(360 / max_range) - 0.5  # -0.5 para aproximar mais
        # Limita entre 12 (cidade) e 18 (rua bem próxima)
        zoom = max(12, min(18, zoom))
    
    # ==========================================
    # 4. DESENHA O MAPA
    # ==========================================
    
    # CD
    if is_valid_br_coord(lat_cd, lon_cd):
        fig.add_trace(go.Scattermapbox(
            lat=[lat_cd], 
            lon=[lon_cd], 
            mode="markers",
            marker=dict(size=18, color="black", symbol="circle"),
            name="🏢 CD",
            text=["Centro de Distribuição"],
            hoverinfo="text",
        ))
    
    # Cores
    cores = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
             "#46f0f0", "#f032e6", "#bcf60c", "#fabebe", "#008080"]
    
    cor_idx = 0
    
    for setor in resultado["setores"]:
        if setor["erro"]:
            st.warning(f"Setor '{setor['nome']}': {setor['erro']}")
            continue
        
        for r in setor["rotas"]:
            if not r["paradas"]:
                continue
                
            cor = cores[cor_idx % len(cores)]
            cor_idx += 1
            
            # Filtra paradas válidas
            paradas_validas = [p for p in r["paradas"] 
                              if is_valid_br_coord(p["lat"], p["lon"])]
            
            if not paradas_validas:
                continue
            
            # --- Rota: CD -> paradas -> CD ---
            lats_rota = [lat_cd] + [p["lat"] for p in paradas_validas] + [lat_cd]
            lons_rota = [lon_cd] + [p["lon"] for p in paradas_validas] + [lon_cd]
            
            # Filtra coordenadas válidas
            coords_rota = [(lat, lon) for lat, lon in zip(lats_rota, lons_rota)
                          if is_valid_br_coord(lat, lon)]
            
            if coords_rota and len(coords_rota) > 2:
                lats_filtrados = [lat for lat, lon in coords_rota]
                lons_filtrados = [lon for lat, lon in coords_rota]
                
                # Desenha a linha da rota
                fig.add_trace(go.Scattermapbox(
                    lat=lats_filtrados,
                    lon=lons_filtrados,
                    mode="lines",
                    line=dict(width=3, color=cor),
                    name=f"{setor['nome']} - Veículo {r['veiculo']} ({r['distancia_km']:.0f} km)",
                    hoverinfo="none",
                ))
            
            # --- Marcadores das paradas ---
            paradas_lats = [p["lat"] for p in paradas_validas]
            paradas_lons = [p["lon"] for p in paradas_validas]
            
            textos = []
            for p in paradas_validas:
                endereco = f"{p.get('logradouro', '')}, {p.get('numero', '')}"
                bairro = p.get('bairro', '')
                texto = f"<b>Pedido {p['pedido']}</b><br>{p['cidade']}/{p['uf']}"
                if endereco:
                    texto += f"<br>{endereco}"
                if bairro:
                    texto += f"<br>{bairro}"
                textos.append(texto)
            
            fig.add_trace(go.Scattermapbox(
                lat=paradas_lats,
                lon=paradas_lons,
                mode="markers+text",
                marker=dict(size=14, color=cor, symbol="circle"),
                text=textos,
                textposition="top right",
                textfont=dict(size=11),
                name=f"📍 {setor['nome']} - Paradas",
                hoverinfo="text",
            ))
    
    # ==========================================
    # 5. CONFIGURAÇÃO FINAL
    # ==========================================
    fig.update_layout(
        mapbox=dict(
            style="open-street-map",
            center=dict(lat=centro_lat, lon=centro_lon),
            zoom=zoom,
        ),
        margin=dict(l=0, r=0, t=0, b=0),
        height=600,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=-0.15,
            font=dict(size=10),
        ),
    )
    
    st.plotly_chart(fig, use_container_width=True)
    
    # ==========================================
    # 6. INFO DO ZOOM (debug)
    # ==========================================
    col_info1, col_info2, col_info3 = st.columns(3)
    with col_info1:
        st.metric("📍 Centro", f"{centro_lat:.4f}, {centro_lon:.4f}")
    with col_info2:
        st.metric("🔍 Zoom", f"{zoom:.1f}")
    with col_info3:
        st.metric("📍 Coordenadas", f"{len(coords_validas)} pontos")
    
    if use_real_routes:
        st.caption("🌐 Rotas calculadas com OSRM (OpenStreetMap) - seguindo ruas e estradas reais")
    else:
        st.caption("📏 Rotas em linha reta (haversine) - modo rápido para testes")

