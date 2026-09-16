"""
Pipeline de previsão de demanda semanal por (CD_Atribuido, Nivel_Rota),
seguindo a MESMA metodologia usada em backtest_geral.ipynb /
Demanda_Produto.ipynb do projeto de vendas:

    - Features: Lag_1, Lag_2, Lag_3, Media_3 (média móvel), sazonalidade
      via seno/cosseno (aqui, da semana do ano em vez do mês)
    - Modelo: HistGradientBoostingRegressor (mesmos hiperparâmetros do
      backtest_geral: max_iter=200, learning_rate=0.05, max_depth=6)
    - Backtest por ano (janela expansível: treina com tudo antes do ano X,
      testa no ano X) — mesma lógica de rodar_backtest()
    - Um modelo único cobre todos os grupos (CD x Nivel_Rota), que entram
      como variáveis categóricas (one-hot) — equivalente ao nível
      "produto_canal" do projeto de vendas

Salva:
    - backtest_demanda_rotas   (equivalente a backtest_multinivel)
    - previsao_demanda_rotas   (equivalente a previsao_demanda: previsão
      recursiva das próximas N semanas por grupo, usando o modelo final
      treinado com todo o histórico)

Requisitos:
    pip install pandas scikit-learn --break-system-packages

Uso:
    python prever_demanda_semanal.py caminho_do_banco.db
    python prever_demanda_semanal.py caminho_do_banco.db --semanas-futuro 8
"""

import argparse
import sqlite3
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

TABELA_DEMANDA = "demanda_semanal"
TABELA_BACKTEST = "backtest_demanda_rotas"
TABELA_PREVISAO = "previsao_demanda_rotas"

COLUNAS_GRUPO = ["CD_Atribuido", "Nivel_Rota"]


def preparar_features(df):
    df = df.sort_values(COLUNAS_GRUPO + ["Data_Inicio_Semana"]).copy()
    df["Data_Inicio_Semana"] = pd.to_datetime(df["Data_Inicio_Semana"])

    grp = df.groupby(COLUNAS_GRUPO)["Qtd_Pedidos"]
    df["Lag_1"] = grp.transform(lambda x: x.shift(1))
    df["Lag_2"] = grp.transform(lambda x: x.shift(2))
    df["Lag_3"] = grp.transform(lambda x: x.shift(3))
    df["Media_3"] = grp.transform(lambda x: x.shift(1).rolling(3).mean())

    df["Semana_sin"] = np.sin(2 * np.pi * df["Semana_ISO"] / 52)
    df["Semana_cos"] = np.cos(2 * np.pi * df["Semana_ISO"] / 52)

    return df


def montar_X_y(df_feat):
    df_enc = pd.get_dummies(df_feat, columns=COLUNAS_GRUPO, prefix=COLUNAS_GRUPO)
    colunas_excluir = ["Qtd_Pedidos", "Data_Inicio_Semana", "Ano_ISO", "Semana_ISO"]
    colunas_x = [c for c in df_enc.columns if c not in colunas_excluir]
    return df_enc, colunas_x


def rodar_backtest(df_feat, colunas_x):
    print("\n🚀 Backtest: demanda semanal por CD x Nível de rota")
    resultados = []
    resultados_por_grupo = []

    df_valido = df_feat.dropna(subset=["Lag_1", "Lag_2", "Lag_3", "Media_3"])
    anos = sorted(df_valido["Ano_ISO"].unique())

    inicio_teste = anos[2] if len(anos) > 2 else anos[-1]  # pelo menos 2 anos de warmup

    for ano_teste in [a for a in anos if a >= inicio_teste]:
        train = df_valido[df_valido["Ano_ISO"] < ano_teste]
        test = df_valido[df_valido["Ano_ISO"] == ano_teste]
        if len(train) == 0 or len(test) == 0:
            continue

        df_enc, cols_x = montar_X_y(df_valido)
        X_train = df_enc.loc[train.index, cols_x]
        y_train = train["Qtd_Pedidos"]
        X_test = df_enc.loc[test.index, cols_x]
        y_test = test["Qtd_Pedidos"]

        model = HistGradientBoostingRegressor(
            loss="poisson", max_iter=200, learning_rate=0.05, max_depth=6, random_state=42
        )
        model.fit(X_train, y_train)
        y_pred = model.predict(X_test)

        mae = mean_absolute_error(y_test, y_pred)
        media_real = y_test.mean()
        wape = (np.abs(y_test - y_pred).sum() / y_test.sum()) * 100 if y_test.sum() > 0 else float("nan")

        # erro % médio DE VERDADE por grupo (não o agregado do teste inteiro, que é igual ao WAPE)
        test_pred_ano = test.copy()
        test_pred_ano["Pred"] = y_pred
        erros_grupo = []
        for _, df_g in test_pred_ano.groupby(COLUNAS_GRUPO):
            mae_g = mean_absolute_error(df_g["Qtd_Pedidos"], df_g["Pred"])
            media_g = df_g["Qtd_Pedidos"].mean()
            if media_g > 0:
                erros_grupo.append((mae_g / media_g) * 100)
        erro_pct_medio_grupo = float(np.mean(erros_grupo)) if erros_grupo else float("nan")

        print(f"📅 {ano_teste} | MAE: {mae:.2f} | Erro % (média simples por grupo): {erro_pct_medio_grupo:.2f} | WAPE (ponderado): {wape:.2f}")
        resultados.append({
            "Nivel": "CD_Nivel_Rota", "Ano": int(ano_teste),
            "MAE": mae, "Erro_%_medio_por_grupo": erro_pct_medio_grupo, "WAPE_%": wape, "Qtd": len(test),
        })

        # quebra por grupo (CD + Nivel_Rota), só no último ano de teste, para diagnóstico
        test_pred = test.copy()
        test_pred["Pred"] = y_pred
        if ano_teste == anos[-1]:
            for grupo, df_g in test_pred.groupby(COLUNAS_GRUPO):
                mae_g = mean_absolute_error(df_g["Qtd_Pedidos"], df_g["Pred"])
                media_g = df_g["Qtd_Pedidos"].mean()
                erro_g = (mae_g / media_g) * 100 if media_g > 0 else float("nan")
                resultados_por_grupo.append({
                    "CD_Atribuido": grupo[0], "Nivel_Rota": grupo[1],
                    "Ano": int(ano_teste), "MAE": mae_g, "Erro_%": erro_g,
                    "Media_Qtd_Real": media_g, "Qtd_Semanas_Teste": len(df_g),
                })

    print("\n📋 Erro por grupo (último ano de teste):")
    df_grupo = pd.DataFrame(resultados_por_grupo).sort_values("Erro_%", ascending=False)
    print(df_grupo.to_string(index=False))

    return pd.DataFrame(resultados), df_grupo


def prever_futuro(df_feat, colunas_x, semanas_futuro):
    """Treina no histórico completo e prevê recursivamente as próximas N semanas por grupo."""
    print(f"\n🔮 Prevendo as próximas {semanas_futuro} semanas por grupo...")

    df_valido = df_feat.dropna(subset=["Lag_1", "Lag_2", "Lag_3", "Media_3"])
    df_enc_completo, cols_x = montar_X_y(df_valido)

    model = HistGradientBoostingRegressor(
        loss="poisson", max_iter=200, learning_rate=0.05, max_depth=6, random_state=42
    )
    model.fit(df_enc_completo[cols_x], df_valido["Qtd_Pedidos"])

    previsoes = []
    for grupo, df_grupo in df_feat.groupby(COLUNAS_GRUPO):
        cd, nivel = grupo
        historico = df_grupo.sort_values("Data_Inicio_Semana")["Qtd_Pedidos"].tolist()
        ultima_data = df_grupo["Data_Inicio_Semana"].max()

        for passo in range(1, semanas_futuro + 1):
            data_prevista = ultima_data + timedelta(weeks=passo)
            ano_iso, semana_iso, _ = data_prevista.isocalendar()

            lag_1 = historico[-1]
            lag_2 = historico[-2] if len(historico) >= 2 else lag_1
            lag_3 = historico[-3] if len(historico) >= 3 else lag_2
            media_3 = np.mean(historico[-3:])

            linha = {c: 0 for c in cols_x}
            for col in cols_x:
                if col == f"CD_Atribuido_{cd}":
                    linha[col] = 1
                elif col == f"Nivel_Rota_{nivel}":
                    linha[col] = 1
            linha["Lag_1"], linha["Lag_2"], linha["Lag_3"], linha["Media_3"] = lag_1, lag_2, lag_3, media_3
            linha["Semana_sin"] = np.sin(2 * np.pi * semana_iso / 52)
            linha["Semana_cos"] = np.cos(2 * np.pi * semana_iso / 52)

            X_novo = pd.DataFrame([linha])[cols_x]
            pred = max(0, model.predict(X_novo)[0])

            previsoes.append({
                "CD_Atribuido": cd, "Nivel_Rota": nivel,
                "Ano_ISO": ano_iso, "Semana_ISO": semana_iso,
                "Data_Inicio_Semana": data_prevista.date().isoformat(),
                "Previsao": round(pred, 2),
            })
            historico.append(pred)

    return pd.DataFrame(previsoes)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    parser.add_argument("--semanas-futuro", type=int, default=8)
    args = parser.parse_args()

    engine_path = f"sqlite:///{args.db_path}"
    conn = sqlite3.connect(args.db_path)

    print("📊 Carregando demanda_semanal...")
    df = pd.read_sql(f"SELECT * FROM {TABELA_DEMANDA}", conn)
    print(f"✅ Dados: {df.shape}")

    df_feat = preparar_features(df)
    df_enc, cols_x = montar_X_y(df_feat.dropna(subset=["Lag_1", "Lag_2", "Lag_3", "Media_3"]))

    df_backtest, df_backtest_grupo = rodar_backtest(df_feat, cols_x)
    print("\n📊 Resultado consolidado do backtest:")
    print(df_backtest.groupby("Ano")[["MAE", "Erro_%_medio_por_grupo", "WAPE_%"]].mean())

    df_backtest["Data_Execucao"] = datetime.now().isoformat()
    df_backtest.to_sql(TABELA_BACKTEST, conn, if_exists="replace", index=False)
    print(f"\n✅ Salvo no banco: {TABELA_BACKTEST}")

    df_backtest_grupo["Data_Execucao"] = datetime.now().isoformat()
    df_backtest_grupo.to_sql(f"{TABELA_BACKTEST}_por_grupo", conn, if_exists="replace", index=False)
    print(f"✅ Salvo no banco: {TABELA_BACKTEST}_por_grupo")

    df_previsao = prever_futuro(df_feat, cols_x, args.semanas_futuro)
    df_previsao["Data_Execucao"] = datetime.now().isoformat()
    df_previsao.to_sql(TABELA_PREVISAO, conn, if_exists="replace", index=False)
    print(f"✅ Salvo no banco: {TABELA_PREVISAO} ({len(df_previsao)} linhas)")

    conn.close()
    print("\n🚀 Pipeline completo executado com sucesso!")


if __name__ == "__main__":
    main()
