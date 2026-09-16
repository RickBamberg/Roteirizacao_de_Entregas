# Roteirização de Entregas — Previsão de Demanda + Otimização de Rotas (VRP)

Projeto de portfólio que simula, sobre uma base real de vendas de varejo, a
operação logística completa de uma rede de centros de distribuição: da
geografia dos clientes até a otimização das rotas de entrega, passando por
regras de despacho em lote e previsão de demanda.

O projeto nasceu de uma lacuna real: anos de experiência em logística de
varejo (incluindo os CDs de Salvador, Recife, João Pessoa, Manaus e Fortaleza 
de uma grande rede de eletrodomésticos), sem nenhum projeto de ciência de dados
que refletisse esse conhecimento de negócio. A ideia foi trazer essa vivência 
prática para dentro dos dados — não só rodar um modelo, mas modelar a operação 
como ela de fato funciona.

## Cenário

Uma empresa de varejo com centros de distribuição (CDs) regionais atende
clientes espalhados pelo Brasil. Cada CD atende sua própria UF e as UFs
vizinhas, classificando cada entrega num nível de rota:

- **Dentro da capital** — mesma cidade do CD
- **Região metropolitana** — até ~60 km da capital
- **Interior do estado** — mesma UF do CD, fora da região metropolitana
- **Estado vizinho** — UF diferente, mas faz fronteira com a UF do CD

Cada nível tem seu próprio SLA de despacho (prazo máximo de espera no CD
antes do lote sair, mesmo incompleto) e seu próprio perfil de veículo —
da van de entrega local ao comboio de carretas para estado vizinho.

## Pipeline

O projeto parte de uma base de vendas já existente (usada em um projeto
anterior de análise de vendas), que **não tinha nenhuma informação
geográfica de verdade** (endereço, CD, distância). Cada etapa abaixo supre
uma peça que faltava:

| # | Script | O que faz |
|---|--------|-----------|
| 1 | `redistribuir_cidades_por_populacao.py` | Substitui a distribuição sintética de cidades (só 31 municípios cadastrados, alguns estados 100% concentrados numa cidade) por municípios reais do IBGE, redistribuindo clientes com peso por população |
| 2 | `gerar_enderecos_ficticios_v2.py` | Gera endereço fictício + coordenada (lat/lon com jitter) para cada cliente, e classifica seu nível de rota relativo a um CD |
| 3 | `classificar_rotas_multi_cd.py` | Versão multi-CD: atribui cada cliente ao CD mais adequado (própria UF > UF vizinha mais próxima) entre vários CDs informados |
| 4 | `criar_centros_distribuicao.py` | Cria a tabela formal dos CDs (localização = capital da UF) |
| 5 | `simular_despacho_lotes.py` | Simula o despacho em lote por (CD, nível de rota): lote sai quando enche OU quando o SLA estoura, o que vier primeiro. Gera `Tempo_Entrega_Dias` = espera no CD + viagem |
| 6 | `preparar_demanda_semanal.py` | Agrega vendas em série semanal por (CD, nível de rota), com calendário completo (zeros explícitos, sem buracos) |
| 7 | `prever_demanda_semanal.py` | Modelo de previsão de demanda (mesma metodologia do projeto de vendas: HistGradientBoostingRegressor, lags, sazonalidade, backtest por ano) |
| 8 | `otimizar_rotas_vrp.py` | Resolve o VRP capacitado (OR-Tools) para um lote real, com setorização geográfica (KMeans, manual ou automática) para lotes grandes. Distância calculada via OSRM (rodovia real) quando disponível, com fallback automático para haversine. Suporta saída em texto (CLI) ou JSON (`--json`, usado pelo dashboard) |
| 9 | `dashboard_rotas.py` | Dashboard interativo (Streamlit + Plotly): filtra por CD e nível de rota, mostra os lotes disponíveis, deixa escolher um lote e roda a otimização com mapa da rota (geometria real de rodovia via OSRM, quando disponível) |

Scripts auxiliares: `corrigir_latitude_cidades.py` (correção pontual de bug),
`diagnosticar_enderecos_bahia.py` e `corrigir_enderecos_na_agua.py` (detectam
e corrigem endereços fictícios que caíram fora da malha viária — ver seção
OSRM abaixo).

## Configuração final

**6 CDs**, cobrindo os 18.484 clientes / 20 UFs da base, com 0 clientes fora
de área: **BA, CE, SP, SC, AM, PA**.

## Decisões e correções pelo caminho

Vale documentar porque fazem parte do resultado tanto quanto os números:

- **Concentração geográfica artificial**: a base original tinha só 31
  municípios cadastrados (ex: toda a Bahia em 1 cidade só). Resolvido
  redistribuindo por município real do IBGE, ponderado por população.
- **Bug de parsing do CSV de municípios**: a detecção de coluna de latitude
  pegou `osm_relation_id` por engano (a palavra "relat**ion**" contém "lat"
  como substring). Corrigido com correspondência por palavra inteira +
  validação de faixa geográfica do Brasil.
- **Tempo de entrega original sem sinal geográfico**: `Data_Entrega` da base
  original era essencialmente aleatório (~17,3 a 17,5 dias em todos os
  níveis de rota). Substituído por uma simulação de despacho em lote
  (espera no CD + viagem), calibrada com os prazos reais informados
  (capital: 1 dia; metropolitana: 2-3; interior: 3-5; vizinho: até 5).
- **Capacidade de lote subestimada**: a primeira estimativa de "lote cheio"
  (1,5x a média histórica) gerava lotes de 2-3 pedidos, disparando o
  despacho quase imediato mesmo em rotas de baixo volume. Corrigido com uma
  capacidade mínima realista.
- **Métrica de erro distorcida por grupos pequenos**: o erro percentual
  médio simples por grupo (~55-68%) é inflado por rotas de baixíssimo
  volume (ex: 0,3 pedido/semana). O WAPE (erro absoluto total / demanda
  real total, ~25-27%) é a leitura mais justa do desempenho real do modelo.
- **VRP misturando níveis de rota**: rodar o VRP só por CD+data (sem
  filtrar nível de rota) produzia rotas fisicamente inviáveis, misturando
  entrega local com viagem interestadual. Corrigido com filtro obrigatório
  por nível de rota.
- **Setorização nem sempre ajuda**: dividir um lote pequeno (que já cabe
  num único veículo) em vários setores só adiciona viagens de ida/volta
  redundantes ao CD. O dashboard tem um modo "escolher automaticamente" que
  testa várias quantidades de setor e fica com a de menor distância total —
  em lotes pequenos, 1 setor costuma vencer.
- **OR-Tools + Streamlit no Windows**: importar `otimizar_rotas_vrp.py`
  direto dentro do processo do Streamlit falhava ao carregar a DLL nativa
  do OR-Tools (o file watcher do Streamlit interfere no carregamento).
  Resolvido rodando a otimização como processo separado (`subprocess`),
  com o script devolvendo o resultado em JSON (`--json`) para o dashboard
  consumir.
- **Encoding no Windows**: o mesmo subprocess falhava silenciosamente
  (`stdout` vinha `None`) ao imprimir nomes de cidade acentuados, porque o
  console do Windows não usa UTF-8 por padrão. Corrigido forçando
  `PYTHONIOENCODING=utf-8` no processo filho e tratamento de erro mais
  defensivo no dashboard.
- **Zoom do mapa fixo**: o mapa sempre abria no zoom de país, então rotas
  pequenas (dentro da capital) apareciam como um ponto único, ilegível.
  Corrigido calculando o zoom automaticamente a partir da dispersão real
  dos pontos de cada rota.


## Resultados

- **Previsão de demanda**: WAPE ~25-27% no backtest por ano (2012-2021)
- **VRP**: 65-85% de economia de distância vs. entrega dedicada por pedido,
  dependendo do nível de rota e volume do lote testado
- **Dashboard**: interface interativa (filtro de CD + nível de rota →
  tabela de lotes → mapa da rota otimizada com zoom automático e sequência
  de paradas com endereço completo)
- **Distância real de rodovia**: OSRM self-hosted (Bahia) validado contra
  a distância em linha reta em 3 cenários (dentro da capital, interior,
  estado vizinho com fallback), com geometria real desenhada no mapa

## Distância real de rodovia (OSRM self-hosted)

O VRP originalmente usava distância haversine (linha reta) entre pontos —
simples, mas irreal: ignora ruas de mão única, contornos de baía, e
qualquer obstáculo geográfico. A alternativa escolhida foi rodar um
servidor **OSRM** (Open Source Routing Machine) localmente via Docker,
calculando distância real de rodovia sem depender de API paga ou de
terceiros.

**Instalação:**

1. Baixar o extrato do Brasil (`.osm.pbf`) do
   [Geofabrik](https://download.geofabrik.de/south-america/brazil.html) e
   colocar numa pasta `osrm-data/` na raiz do projeto.

2. Recortar só a Bahia (Geofabrik não oferece extrato por estado — ver
   "Problemas encontrados" abaixo) usando `osmium extract`:
   ```
   docker run -it -v "${PWD}/osrm-data:/data" wangyoucao577/osmium-tool extract -b -46.62,-18.35,-37.34,-8.53 /data/brazil-latest.osm.pbf -o /data/bahia.osm.pbf
   ```
   (`-b` é a caixa delimitadora aproximada da Bahia, no formato
   `min_lon,min_lat,max_lon,max_lat`.)

3. Pré-processar o grafo de roteamento com três comandos, em sequência
   (cada um só roda depois que o anterior termina):
   ```
   docker run -t -v "${PWD}/osrm-data:/data" osrm/osrm-backend osrm-extract -p /opt/car.lua /data/bahia.osm.pbf
   docker run -t -v "${PWD}/osrm-data:/data" osrm/osrm-backend osrm-partition /data/bahia.osrm
   docker run -t -v "${PWD}/osrm-data:/data" osrm/osrm-backend osrm-customize /data/bahia.osrm
   ```
   Esse pré-processamento só precisa ser feito uma vez (ou de novo, se o
   `.osm.pbf` mudar).

4. Subir o servidor, expondo a API HTTP na porta 5000:
   ```
   docker run -t -i -p 5000:5000 -v "${PWD}/osrm-data:/data" osrm/osrm-backend osrm-routed --algorithm mld /data/bahia.osrm
   ```
   **Importante:** diferente do pré-processamento (passo 3), esse
   comando não termina — ele deixa o terminal ocupado rodando o
   servidor. Ele **precisa continuar ativo** (nesse terminal, ou em
   segundo plano) enquanto `otimizar_rotas_vrp.py` ou `dashboard_rotas.py`
   estiverem sendo usados — são eles que fazem as consultas HTTP pra
   `localhost:5000` a cada cálculo de rota. Se o servidor for encerrado,
   os scripts caem automaticamente no fallback de distância em linha
   reta (haversine), sem travar, mas sem a precisão real de rodovia.

5. Consultar via `/table` (matriz de distância, usada pelo VRP) e
   `/route` (geometria do trajeto, usada só pro mapa).

**Problemas encontrados:**

- **Falta de memória processando o Brasil inteiro.** A máquina de
  desenvolvimento tem só 10GB de RAM (DDR3) — o `osrm-extract` do país
  inteiro (e até de um extrato regional, "Nordeste") foi morto por falta
  de memória (OOM) repetidas vezes, mesmo fechando o Docker Desktop e
  outros programas.
- **Geofabrik não oferece extrato por estado para o Brasil** (só o país
  inteiro, diferente de outros países que têm subdivisão estadual).
  Resolvido recortando o `brazil-latest.osm.pbf` já baixado com `osmium
  extract` e um bounding box manual da Bahia, gerando um arquivo ~12x
  menor (174MB vs. 2GB) — processou sem estourar memória (pico de
  ~3,4GB).
- **OSRM não recusa coordenadas fora da área carregada** — ele "gruda"
  (snap) no nó de rua mais próximo dentro do grafo disponível e responde
  `code: "Ok"` mesmo assim, calculando uma distância enganosamente
  plausível. Isso fazia o nível "Estado vizinho" (que sai da Bahia)
  receber distâncias erradas sem nenhum aviso. Corrigido checando o
  bounding box do recorte antes de consultar o OSRM — se algum ponto do
  lote cai fora da área, o lote inteiro usa haversine (com aviso
  explícito no terminal), em vez de confiar cegamente na resposta do
  OSRM.
- **O mapa do dashboard desenhava linha reta entre paradas**, mesmo com
  distância real calculada — o endpoint usado pro cálculo (`/table`) só
  devolve números, não o trajeto. Em Salvador, isso fazia a "rota" cortar
  direto pela Baía de Todos os Santos. Corrigido buscando a geometria
  real via `/route` (outro endpoint) e desenhando essa geometria no mapa,
  com fallback pra linha reta só nos casos que já usam haversine.
- **A geometria real revelou um bug pré-existente no gerador de
  endereços fictícios**: o jitter aleatório em volta do centro da cidade
  não distingue terra de água, então algumas coordenadas caíam
  literalmente no mar — invisível na versão anterior (linha reta), só
  apareceu quando o trajeto passou a contornar a costa de verdade.
  Diagnosticado com o endpoint `/nearest` do OSRM (distância até a rua
  mais próxima: se for grande, é sinal de ponto isolado n'água) e
  corrigido regenerando um novo ponto validado contra a malha viária
  real, com raio de busca crescente e limiar de aceitação apertado
  (100m) para não deixar pontos perto de enseadas estreitas. Um teste
  inicial com limiar frouxo (1km) deixou passar alguns casos em enseadas
  estreitas; refeito com limiar de 0,2km e filtro por cidade
  litorânea/ribeirinha (evitando falsos positivos em cidades do interior,
  onde a cobertura de rua do OpenStreetMap é naturalmente esparsa).
- **Achado interessante, não bug**: com distância real de rodovia (que
  não obedece a desigualdade triangular do jeito que a linha reta
  obedece), a rota otimizada de um lote pequeno pode legitimamente sair
  mais longa que a soma das viagens dedicadas por pedido — algo
  impossível de acontecer na versão haversine. Fica registrado como uma
  nuance real da otimização com geografia real, não uma falha de
  cálculo.

**Limitação atual:** o recorte processado cobre só a Bahia — os demais 5
CDs do projeto (CE, SP, SC, AM, PA) continuam usando haversine, assim
como qualquer rota "Estado vizinho" saindo da Bahia. Expandir a cobertura
exigiria recortar e processar mais extratos regionais, replicando o
mesmo pipeline.

## Limitações conhecidas / trabalho futuro

- **Demanda intermitente**: rotas de baixo volume (ex: região metropolitana
  de estados pequenos) teriam previsão mais adequada com uma técnica
  específica para séries com muitos zeros (ex: método de Croston), em vez
  do mesmo modelo usado para os grupos de alto volume.
- **Carga combinada B2B + B2C**: na operação real que inspirou o projeto, a
  mesma carga muitas vezes combinava entrega ao cliente final com
  abastecimento de loja (geladeira, TV, fogão etc.). Modelar isso exigiria
  um cadastro de lojas e uma rotina de reposição — um segundo fluxo de
  demanda que ficou fora do escopo desta primeira versão.
- **Distância real de rodovia limitada à Bahia**: resolvido via OSRM
  self-hosted (ver seção acima), mas só para o CD da Bahia — os outros 5
  CDs e as rotas "Estado vizinho" continuam usando haversine (linha
  reta) até que o mesmo recorte/processamento seja feito para as demais
  regiões.

## Tecnologias

Python, SQLite, pandas, scikit-learn (HistGradientBoostingRegressor,
KMeans), OR-Tools (VRP), Faker (dados fictícios), Streamlit + Plotly
(dashboard), OSRM + Docker + osmium-tool (distância e geometria real de
rodovia).
