import streamlit as st
import requests
import plotly.express as px
import sqlite3
import pandas as pd
import io

st.set_page_config(layout="wide")
st.title("📊 Monitoramento de Chamadas Não Atendidas (API CDR Evence)")

# ===== CONFIGURAÇÃO DA API E BANCO =====
API_TOKEN = "4275c3fd79ac7997e3dc03fb451657518b50d55203c41c8798a3c81eb5825031"
BASE_URL = "https://pabx.evence.com.br/api/v1/cdr"

def init_db():
    conn = sqlite3.connect("cdr_nao_atendidas.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chamadas_perdidas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_hora TEXT,
            origem TEXT,
            destino TEXT,
            ramal_tecnico TEXT,
            duracao TEXT,
            status TEXT,
            tipo TEXT,
            UNIQUE(data_hora, origem, destino, ramal_tecnico)
        )
    """)
    conn.commit()
    conn.close()

init_db()

def salvar_no_banco(registros):
    conn = sqlite3.connect("cdr_nao_atendidas.db")
    cursor = conn.cursor()
    for reg in registros:
        try:
            # Garante que o registro tem o formato esperado
            if len(reg) >= 7:
                data_hora, origem, destino, ramal, duracao, status, tipo = reg[0], reg[1], reg[2], reg[3], reg[4], reg[5], reg[6]
                
                # Filtramos apenas as "Não atendidas" (case insensitive)
                if "não atendida" in str(status).lower():
                    cursor.execute("""
                        INSERT OR IGNORE INTO chamadas_perdidas 
                        (data_hora, origem, destino, ramal_tecnico, duracao, status, tipo)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (data_hora, origem, destino, ramal, duracao, status, tipo))
        except Exception:
            continue
    conn.commit()
    conn.close()

def carregar_do_banco(data_inicio, data_fim):
    conn = sqlite3.connect("cdr_nao_atendidas.db")
    query = "SELECT * FROM chamadas_perdidas"
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if not df.empty:
        df["data_obj"] = pd.to_datetime(df["data_hora"], format="%d-%m-%Y %H:%M:%S", errors="coerce")
        mask = (df["data_obj"].dt.date >= data_inicio) & (df["data_obj"].dt.date <= data_fim)
        df = df.loc[mask]
    return df

# ===== FILTROS NA TELA =====
st.sidebar.header("Filtros de Período")
data_inicio = st.sidebar.date_input("Data início")
data_fim = st.sidebar.date_input("Data fim")

# ===== BOTÃO BUSCAR DA API =====
if st.button("Sincronizar Dados da API Evence"):
    indice = 0
    total_inseridos = 0
    sucesso_busca = False
    
    with st.spinner("Buscando registros na API de CDR..."):
        while True:
            url = f"{BASE_URL}?api_token={API_TOKEN}&datainicio={data_inicio}&datafinal={data_fim}&indice={indice}"
            
            try:
                response = requests.get(url)
                
                # Se der 404, geralmente indica que a paginação acabou ou a rota chegou ao fim
                if response.status_code == 404:
                    break
                
                if response.status_code != 200:
                    st.error(f"Erro na API: Status {response.status_code}")
                    break
                
                data = response.json()
                
                if "error" in data:
                    st.error(f"Erro retornado pela API: {data['error']}")
                    break
                
                cdr_dict = data.get("cdr", {})
                if not cdr_dict:
                    break # Fim dos registros
                
                lista_registros = list(cdr_dict.values())
                salvar_no_banco(lista_registros)
                
                total_inseridos += len(lista_registros)
                indice += len(lista_registros) 
                sucesso_busca = True
                
                # Segurança contra loop infinito caso a API retorne sempre a mesma quantidade
                if len(lista_registros) == 0:
                    break
                    
            except Exception as e:
                st.error(f"Erro de conexão: {e}")
                break
                
    if sucesso_busca or total_inseridos > 0:
        st.success(f"Sincronização concluída! {total_inseridos} registros analisados e salvos.")
    else:
        st.warning("A sincronização finalizou, mas nenhum registro foi retornado pela API para este período.")

# ===== EXIBIÇÃO DOS DADOS DO BANCO =====
df_resultado = carregar_do_banco(data_inicio, data_fim)

if not df_resultado.empty:
    st.subheader(f"Chamadas Não Atendidas por Ramal/Técnico ({data_inicio} a {data_fim})")
    
    contagem = df_resultado["ramal_tecnico"].value_counts().reset_index()
    contagem.columns = ["Ramal / Técnico", "Quantidade Não Atendida"]

    cols = st.columns(4)
    for i, row in contagem.iterrows():
        cols[i % 4].metric(f"Ramal {row['Ramal / Técnico']}", int(row['Quantidade Não Atendida']))

    fig = px.pie(
        contagem,
        names="Ramal / Técnico",
        values="Quantidade Não Atendida",
        title="Proporção de Chamadas Não Atendidas por Ramal"
    )
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Detalhamento das Chamadas Perdidas")
    st.dataframe(df_resultado[["data_hora", "origem", "destino", "ramal_tecnico", "status", "tipo"]])

    st.markdown("---")
    col_exp1, col_exp2 = st.columns(2)

    with col_exp1:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_resultado.to_excel(writer, index=False, sheet_name='Nao Atendidas')
        excel_data = output.getvalue()
        
        st.download_button(
            label="📥 Baixar Relatório em Excel (XLSX)",
            data=excel_data,
            file_name=f"nao_atendidas_{data_inicio}_a_{data_fim}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

else:
    st.info("Nenhum registro de chamada não atendida encontrado para este período no banco local. Selecione um período onde houve chamadas e clique em 'Sincronizar Dados da API Evence'.")
