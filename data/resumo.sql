SELECT count() from vendas

SELECT count() from itens_vendas

SELECT 
    (SELECT COUNT(*) FROM vendas) AS total_vendas,
    (SELECT COUNT(*) FROM itens_vendas) AS total_itens_vendas;

# Faturamento por Categoria 	
SELECT 
    c.Categoria,
    SUM(iv.Valor_Total) AS faturamento
FROM itens_vendas iv
JOIN produtos p ON iv.ID_Produto = p.ID_Produto
JOIN subcategorias s ON p.ID_Subcategoria = s.ID_Subcategoria
JOIN categorias c ON s.ID_Categoria = c.ID_Categoria
GROUP BY c.Categoria;
	
# Tabelão 	
SELECT 
	pd.ID_Pedido as Num_Pedido,
	pd.Data_Venda as Data_Venda,
	pd.Data_Entrega as Data_Entrega,
	p.ID_Produto as Cod_Produto,
	p.Descricao_Produto as Descricao_Produto,
	p.Preco_Unitario as Pco_Unit_Prod,
	p.Tributos as Vlr_Trubuto,
	p.Custo as Vlr_Custo,
	iv.Qtde as Qtde_Pedido,
	iv.Valor_Unitario as Vlr_Unit_Ped,
	iv.Valor_Total as Vlr_Total_Ped,
	c.ID_Categoria as ID_Categoria,
	c.Categoria as Categoria,
	s.ID_Subcategoria as ID_Subcategoria,
	s.Subcategoria as Subcategoria,
	m.ID_Marca as ID_Marca,
	m.Marca as Marca,
	cl.ID_Cliente as ID_Cliente,
	cl.Nome as Nome,
	cl.Email as Email,
	cl.Data_Nascimento as Data_Nascimento,
	cl.Estado_Civil as Estado_Civil,
	cl.Genero as Genero,
	cl.Educacao as Educacao,
	cd.ID_Cidade as ID_Cidade,
	cd.Cidade as Cidade,
	cd.UF as UF,
	cn.ID_Canal as ID_Canal,
	cn.Descricao_Canal as Canal
FROM itens_vendas iv
JOIN vendas pd ON iv.ID_Pedido = pd.ID_Pedido
JOIN produtos p ON iv.ID_Produto = p.ID_Produto
JOIN marcas m ON p.ID_Marca = m.ID_Marca
JOIN subcategorias s ON p.ID_Subcategoria = s.ID_Subcategoria
JOIN categorias c ON s.ID_Categoria = c.ID_Categoria
JOIN clientes cl ON pd.ID_Cliente = cl.ID_Cliente
JOIN cidades cd ON cl.ID_Cidade = cd.ID_Cidade
JOIN canais cn ON pd.ID_Canal = cn.ID_Canal;
	
# Produto por Clientes 	
SELECT 
	cl.ID_Cliente as ID_Cliente,
	cl.Nome as Nome,
	p.ID_Produto as Cod_Produto,
	p.Descricao_Produto as Descricao_Produto,
	p.Preco_Unitario as Pco_Unit_Prod,
	iv.Qtde as Qtde_Pedido,
	iv.Valor_Unitario as Vlr_Unit_Ped,
	iv.Valor_Total as Vlr_Total_Ped,
	c.ID_Categoria as ID_Categoria,
	c.Categoria as Categoria,
	pd.ID_Pedido as Num_Pedido,
	pd.Data_Venda as Data_Venda,
	pd.Data_Entrega as Data_Entrega
FROM itens_vendas iv
JOIN vendas pd ON iv.ID_Pedido = pd.ID_Pedido
JOIN produtos p ON iv.ID_Produto = p.ID_Produto
JOIN clientes cl ON pd.ID_Cliente = cl.ID_Cliente
JOIN subcategorias s ON p.ID_Subcategoria = s.ID_Subcategoria
JOIN categorias c ON s.ID_Categoria = c.ID_Categoria
WHERE pd.ID_Cliente = 5000;
	
delete from itens_vendas
delete from vendas

DROP TABLE IF EXISTS itens_vendas
DROP TABLE IF EXISTS vendas
DROP TABLE IF EXISTS calendario


DROP TABLE IF EXISTS modelo_escolhido  
DROP TABLE IF EXISTS previsao_automl
DROP TABLE IF EXISTS previsao_demanda 
DROP TABLE IF EXISTS previsao_demanda_nivel 
DROP TABLE IF EXISTS previsao_demanda_anual
DROP TABLE IF EXISTS previsao_demanda_produto
DROP TABLE IF EXISTS previsao_final 
DROP TABLE IF EXISTS previsao_pivot 
DROP TABLE IF EXISTS backtest_modelo 
DROP TABLE IF EXISTS backtest_modelo_anual 
DROP TABLE IF EXISTS backtest_multinivel 

SELECT ID_Pedido, COUNT(*) as qtd
FROM vendas
GROUP BY ID_Pedido
HAVING COUNT(*) > 1;

SELECT 
    COUNT(*) as total,
    COUNT(DISTINCT ID_Pedido) as distintos
FROM vendas;

SELECT MIN(Data_Venda) as data_mais_antiga, 
       MAX(Data_Venda) as data_mais_recente 
FROM vendas;

COMMIT


SELECT 
    COUNT(*) as total,
    COUNT(cal.Ano) as com_calendario
FROM vendas pd
LEFT JOIN calendario cal 
    ON pd.Data_Venda = cal.Data;
	
SELECT DISTINCT Data_Venda 
FROM vendas 
LIMIT 20;

SELECT 
    Data_Venda,
    COUNT(*) as qtd
FROM vendas


# 'produto':
SELECT pd.Data_Venda, iv.ID_Produto as ID, iv.Qtde
    FROM itens_vendas iv
    JOIN vendas pd ON iv.ID_Pedido = pd.ID_Pedido
WHERE iv.ID_Produto = 1010

# 'categoria':
SELECT pd.Data_Venda, c.ID_Categoria as ID, SUM(iv.Qtde) as Qtde
    FROM itens_vendas iv
    JOIN vendas pd ON iv.ID_Pedido = pd.ID_Pedido
    JOIN produtos p ON iv.ID_Produto = p.ID_Produto
    JOIN subcategorias s ON p.ID_Subcategoria = s.ID_Subcategoria
    JOIN categorias c ON s.ID_Categoria = c.ID_Categoria
WHERE iv.ID_Produto = 1010
    GROUP BY pd.Data_Venda, c.ID_Categoria

# 'canal':
SELECT pd.Data_Venda, pd.ID_Canal as ID, SUM(iv.Qtde) as Qtde
    FROM itens_vendas iv
    JOIN vendas pd ON iv.ID_Pedido = pd.ID_Pedido
WHERE iv.ID_Produto = 1010
    GROUP BY pd.Data_Venda, pd.ID_Canal

# 'produto_canal':
SELECT pd.Data_Venda, iv.ID_Produto || '_' || pd.ID_Canal as ID, SUM(iv.Qtde) as Qtde
    FROM itens_vendas iv
    JOIN vendas pd ON iv.ID_Pedido = pd.ID_Pedido
WHERE iv.ID_Produto = 1010
    GROUP BY pd.Data_Venda, iv.ID_Produto, pd.ID_Canal
    
#-----------------------------------------------------
#  Validação do script do agente_vendas
#-----------------------------------------------------

# "Quais os 5 produtos mais vendidos em 2021?"

SELECT 
    p.Descricao_Produto,
    SUM(iv.Qtde)        AS Total_Vendido,
    SUM(iv.Valor_Total) AS Receita_Total,
    COUNT(DISTINCT v.ID_Pedido) AS Pedidos_Unicos
FROM itens_vendas iv
JOIN vendas   v ON iv.ID_Pedido  = v.ID_Pedido
JOIN produtos p ON iv.ID_Produto = p.ID_Produto
WHERE strftime('%Y', v.Data_Venda) = '2021'
GROUP BY p.ID_Produto, p.Descricao_Produto
ORDER BY Total_Vendido DESC
LIMIT 5;

# "Qual foi o erro percentual médio do modelo por nível?"

SELECT 
    Nivel,
    ROUND(AVG("Erro_%"), 2) AS Erro_Medio_Pct,
    ROUND(AVG(MAE), 2)      AS MAE_Medio,
    COUNT(*)                AS N_Backtests
FROM backtest_multinivel
GROUP BY Nivel
ORDER BY Erro_Medio_Pct;

# "Qual modelo foi escolhido para o nível produto?"

SELECT 
    Nivel,
    Modelo,
    Vencedor,
    ROUND(MAE,    2) AS MAE,
    ROUND("Erro_%", 2) AS Erro_Pct,
    ROUND(RMSE,   2) AS RMSE,
    ROUND(R2,     2) AS R2,
    N_Treino,
    N_Teste,
    Data_Execucao
FROM modelo_escolhido
WHERE Nivel = 'produto'
ORDER BY Vencedor DESC;

# "Qual a previsão de demanda para o produto 1010 nos próximos meses?"

SELECT 
    p.Descricao_Produto,
    pd.Ano,
    pd.Mes,
    ROUND(pd.Previsao, 2) AS Previsao
FROM previsao_demanda pd
JOIN produtos p ON pd.ID_Produto = p.ID_Produto
WHERE pd.ID_Produto = 1010
ORDER BY pd.Ano, pd.Mes;

# "Quais os 10 clientes que mais compraram em valor total? Mostre nome, cidade, UF e total gasto."

SELECT cl.Nome, ci.Cidade, ci.UF,
       ROUND(SUM(iv.Valor_Total), 2) AS Total_Gasto
FROM vendas v
JOIN itens_vendas iv ON v.ID_Pedido  = iv.ID_Pedido
JOIN clientes cl     ON v.ID_Cliente = cl.ID_Cliente
JOIN cidades ci      ON cl.ID_Cidade = ci.ID_Cidade
GROUP BY cl.ID_Cliente
ORDER BY Total_Gasto DESC
LIMIT 10;

# "Qual o faturamento total por canal de venda em cada ano? Mostre canal, ano e receita."

SELECT c.Descricao_Canal,
       strftime('%Y', v.Data_Venda)  AS Ano,
       ROUND(SUM(iv.Valor_Total), 2) AS Receita
FROM vendas v
JOIN itens_vendas iv ON v.ID_Pedido = iv.ID_Pedido
JOIN canais c        ON v.ID_Canal  = c.ID_Canal
GROUP BY c.ID_Canal, Ano
ORDER BY Ano, Receita DESC;

# "Quais as 5 categorias de produto mais vendidas em quantidade? Inclua subcategoria e total de unidades."

SELECT cat.Categoria, sub.Subcategoria,
       SUM(iv.Qtde) AS Total_Unidades
FROM itens_vendas iv
JOIN produtos p      ON iv.ID_Produto     = p.ID_Produto
JOIN subcategorias sub ON p.ID_Subcategoria = sub.ID_Subcategoria
JOIN categorias cat    ON sub.ID_Categoria  = cat.ID_Categoria
GROUP BY cat.ID_Categoria, sub.ID_Subcategoria
ORDER BY Total_Unidades DESC
LIMIT 5;

# "Gere um gráfico de vendas mensais por canal nos últimos 2 anos",
# "Exporte a previsão dos próximos meses para Excel",

SELECT
p.ID_Produto,
p.Descricao_Produto,
cat.Categoria,
sub.Subcategoria,
m.Marca,
pd.Ano,
CASE pd.Mes
WHEN 1 THEN 'Janeiro'
WHEN 2 THEN 'Fevereiro'
WHEN 3 THEN 'Março'
WHEN 4 THEN 'Abril'
WHEN 5 THEN 'Maio'
WHEN 6 THEN 'Junho'
WHEN 7 THEN 'Julho'
WHEN 8 THEN 'Agosto'
WHEN 9 THEN 'Setembro'
WHEN 10 THEN 'Outubro'
WHEN 11 THEN 'Novembro'
WHEN 12 THEN 'Dezembro'
END AS Mes_Nome,
pd.Mes AS Mes_Numero,
pd.Previsao AS Quantidade_Prevista,
ROUND(pd.Previsao * p.Preco_Unitario, 2) AS Receita_Prevista,
ROUND(pd.Previsao * p.Custo, 2) AS Custo_Previsto,
ROUND(pd.Previsao * (p.Preco_Unitario - p.Custo), 2) AS Lucro_Previsto,
ROUND(((p.Preco_Unitario - p.Custo) / p.Preco_Unitario * 100), 2) AS Margem_Percentual,
p.Preco_Unitario,
p.Custo
FROM previsao_demanda pd
JOIN produtos p ON pd.ID_Produto = p.ID_Produto
LEFT JOIN subcategorias sub ON p.ID_Subcategoria = sub.ID_Subcategoria
LEFT JOIN categorias cat ON sub.ID_Categoria = cat.ID_Categoria
LEFT JOIN marcas m ON p.ID_Marca = m.ID_Marca
WHERE pd.Ano >= 2022
ORDER BY p.ID_Produto, pd.Ano, pd.Mes;

SELECT 
    p.ID_Produto,
    p.Descricao_Produto,
    pd.Ano,
    pd.Mes,
    pd.Previsao,
    ROUND(pd.Previsao * p.Preco_Unitario, 2) AS Receita_Prevista
FROM previsao_demanda pd
JOIN produtos p ON pd.ID_Produto = p.ID_Produto
WHERE pd.Ano = 2022
LIMIT 10;


# "Gere um gráfico de barras com o faturamento total por categoria de produto.",

SELECT 
    cat.Categoria,
    ROUND(SUM(iv.Valor_Total)/1000000, 2) AS Faturamento_Milhoes
FROM itens_vendas iv
JOIN produtos p ON iv.ID_Produto = p.ID_Produto
JOIN subcategorias sub ON p.ID_Subcategoria = sub.ID_Subcategoria
JOIN categorias cat ON sub.ID_Categoria = cat.ID_Categoria
GROUP BY cat.ID_Categoria
ORDER BY Faturamento_Milhoes DESC

# Clente e seu enereços


SELECT * 
	FROM clientes cli
JOIN cidades cid ON cli.ID_Cidade = cid.ID_Cidade
JOIN enderecos end ON cli.ID_Endereco = end.ID_Endereco;

SELECT cli.ID_Cliente, 
	   cli.Nome,
	   cli.ID_Cidade,
	   cid.Cidade,
	   cid.UF,
	   end.ID_Endereco,
	   end.Logradouro,
	   end.Bairro,
	   end.CEP
	FROM clientes cli
JOIN cidades cid ON cli.ID_Cidade = cid.ID_Cidade
JOIN enderecos end ON cli.ID_Endereco = end.ID_Endereco
WHERE cid.UF = "BA"
ORDER BY cli.ID_Cidade;

SELECT count(*) 
	FROM clientes cli
JOIN cidades cid ON cli.ID_Cidade = cid.ID_Cidade
WHERE cid.UF = "SP";
# 604
# 1731

SELECT count(*) 
	FROM clientes cli
JOIN cidades cid ON cli.ID_Cidade = cid.ID_Cidade
JOIN enderecos end ON cli.ID_Endereco = end.ID_Endereco
WHERE cid.UF = "SP" AND
      cid.Cidade = "São Paulo";
#604
# 560 em São Paulo

SELECT count(*) FROM cidades;
# 31

SELECT count(*) FROM clientes;
# 18484

SELECT count(*) FROM enderecos;
# 18484

# Quantidade de Cidades por UF 
SELECT
    c.UF,
    COUNT(DISTINCT cl.ID_Cidade) AS qtd_cidades_distintas,
    COUNT(*) AS qtd_clientes
FROM clientes cl
JOIN cidades c ON c.ID_Cidade = cl.ID_Cidade
GROUP BY c.UF
ORDER BY qtd_clientes DESC;

# Quantidades de Clientes por Cidade
SELECT
    ci.UF,
    ci.Cidade,
    ci.Populacao,
    ci.Capital,
    COUNT(cl.ID_Cliente) AS qtd_clientes
FROM cidades ci
LEFT JOIN clientes cl ON cl.ID_Cidade = ci.ID_Cidade
GROUP BY ci.ID_Cidade, ci.UF, ci.Cidade, ci.Populacao, ci.Capital
ORDER BY ci.UF, qtd_clientes DESC;

SELECT
    ci.UF,
    COUNT(DISTINCT CASE WHEN cl.ID_Cliente IS NOT NULL THEN ci.ID_Cidade END) AS qtd_cidades_com_cliente,
    COUNT(cl.ID_Cliente) AS qtd_clientes
FROM cidades ci
LEFT JOIN clientes cl ON cl.ID_Cidade = ci.ID_Cidade
GROUP BY ci.UF
ORDER BY qtd_clientes DESC;

SELECT UF, Cidade, Latitude, Longitude FROM centros_distribuicao;

# Tempo médio de entrega por nível de rota
SELECT
    cl.Nivel_Rota,
    COUNT(*) AS qtd_pedidos,
    AVG(julianday(v.Data_Entrega) - julianday(v.Data_Venda)) AS media_dias_entrega,
    MIN(julianday(v.Data_Entrega) - julianday(v.Data_Venda)) AS min_dias,
    MAX(julianday(v.Data_Entrega) - julianday(v.Data_Venda)) AS max_dias
FROM vendas v
JOIN clientes cl ON cl.ID_Cliente = v.ID_Cliente
GROUP BY cl.Nivel_Rota
ORDER BY media_dias_entrega;

# Quantas entregas caem no mesmo dia, por CD
SELECT
    cl.CD_Atribuido,
    v.Data_Entrega,
    COUNT(*) AS qtd_entregas
FROM vendas v
JOIN clientes cl ON cl.ID_Cliente = v.ID_Cliente
GROUP BY cl.CD_Atribuido, v.Data_Entrega
ORDER BY qtd_entregas DESC
LIMIT 30;

# Distribuição geral: quantas entregas por dia costumam acontecer
SELECT
    v.Data_Entrega,
    COUNT(*) AS qtd_entregas,
    COUNT(DISTINCT cl.CD_Atribuido) AS qtd_cds_envolvidos
FROM vendas v
JOIN clientes cl ON cl.ID_Cliente = v.ID_Cliente
GROUP BY v.Data_Entrega
ORDER BY v.Data_Entrega
LIMIT 30;

**********

SELECT * FROM clientes LIMIT  20;
SELECT * FROM cidades;
SELECT * FROM enderecos;
SELECT * FROM centros_distribuicao;
SELECT * FROM modelo_escolhido;

SELECT
    cl.CD_Atribuido,
    cl.Nivel_Rota,
    AVG(v.Dias_Espera_CD) AS media_espera_cd,
    AVG(v.Dias_Viagem) AS media_viagem,
    AVG(v.Tempo_Entrega_Dias) AS media_total,
    COUNT(*) AS qtd_pedidos
FROM vendas v
JOIN clientes cl ON cl.ID_Cliente = v.ID_Cliente
GROUP BY cl.CD_Atribuido, cl.Nivel_Rota
ORDER BY cl.CD_Atribuido, media_total;

# Demanda Semanal
SELECT * FROM demanda_semanal
ORDER BY CD_Atribuido, Nivel_Rota, Ano_ISO, Semana_ISO
LIMIT 30;

SELECT Ano_ISO, Semana_ISO, Data_Inicio_Semana, Qtd_Pedidos
FROM demanda_semanal
WHERE CD_Atribuido = 'AM' AND Nivel_Rota = 'Dentro da capital'
ORDER BY Data_Inicio_Semana
LIMIT 30;

# Data de saida do CD 
SELECT cl.CD_Atribuido, v.Data_Saida_CD, COUNT(*) AS qtd
FROM vendas v JOIN clientes cl ON cl.ID_Cliente = v.ID_Cliente
GROUP BY cl.CD_Atribuido, v.Data_Saida_CD
ORDER BY qtd DESC
LIMIT 10;

SELECT cl.CD_Atribuido, cl.Nivel_Rota, v.Data_Saida_CD, COUNT(*) AS qtd
FROM vendas v JOIN clientes cl ON cl.ID_Cliente = v.ID_Cliente
WHERE cl.CD_Atribuido = 'SP' AND cl.Nivel_Rota = 'Dentro da capital'
GROUP BY v.Data_Saida_CD
ORDER BY qtd DESC
LIMIT 10;


SELECT cl.CD_Atribuido, cl.Nivel_Rota, v.Data_Saida_CD, COUNT(*) AS qtd
FROM vendas v JOIN clientes cl ON cl.ID_Cliente = v.ID_Cliente
WHERE cl.CD_Atribuido = 'SP' AND cl.Nivel_Rota = 'Estado vizinho'
GROUP BY v.Data_Saida_CD
ORDER BY qtd DESC
LIMIT 10;

# Endereço por rota
SELECT
    v.ID_Pedido,
    ci.Cidade,
    ci.UF,
    e.Latitude,
    e.Longitude
FROM vendas v
JOIN clientes cl ON cl.ID_Cliente = v.ID_Cliente
JOIN cidades ci ON ci.ID_Cidade = cl.ID_Cidade
JOIN enderecos e ON e.ID_Endereco = cl.ID_Endereco
WHERE v.ID_Pedido IN (
    91232, 91228, 91269, 91260, 91205, 91238, 91273,
    91201, 91225, 91226, 91196, 91268, 91251, 91263, 91285, 91223, 91266, 91249, 91262, 91279, 91284,
    91259, 91281,
    91277, 91261, 91215, 91258, 91247, 91265, 91252
)
ORDER BY v.ID_Pedido;


SELECT * FROM cidades 
   WHERE Cidade = "Salvador";

SELECT * FROM enderecos 
   WHERE ID_Cidade = 1584
ORDER BY CEP;

# Procura mesmo CEP 
SELECT * 
FROM enderecos 
WHERE CEP IN (
    SELECT CEP 
    FROM enderecos 
    GROUP BY CEP 
    HAVING COUNT(*) > 1
);
OU
SELECT e1.*
FROM enderecos e1
WHERE EXISTS (
    SELECT 1 
    FROM enderecos e2 
    WHERE e2.CEP = e1.CEP 
    AND e2.ID_Endereco <> e1.ID_Endereco
);

