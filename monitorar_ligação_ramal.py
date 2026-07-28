import streamlit as st
import requests
import plotly.express as px
import sqlite3
import pandas as pd
import json

# Configuração inicial da página do Streamlit para largura total
st.set_page_config(layout="wide")
st.title("📊 Monitoramento e Auditoria Avançada de Chamadas (API CDR Evence)")

# ===== CONFIGURAÇÃO DA API E BANCO =====
API_TOKEN = "4275c3fd79ac7997e3dc03fb451657518b50d55203c41c8798a3c81eb5825031"
BASE_URL = "https://pabx.evence.com.br/api/v1/cdr"

def init_db():
    """
    Inicializa o banco de dados SQLite local e cria a tabela 'todas_chamadas' 
    com colunas mapeadas de forma explícita.
    """
    conn = sqlite3.connect("cdr_nao_atendidas.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS todas_chamadas (
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
    """
    Processa os registros da API mapeando diretamente os índices do array JSON 
    para as colunas corretas do banco de dados.
    """
    conn = sqlite3.connect("cdr_nao_atendidas.db")
    cursor = conn.cursor()
    for reg in registros:
        try:
            if len(reg) >= 7:
                data_hora = str(reg[0]) if len(reg) > 0 else ""
                origem = str(reg[1]) if len(reg) > 1 else ""
                destino = str(reg[2]) if len(reg) > 2 else ""
                ramal_tecnico = str(reg[3]) if len(reg) > 3 else ""
                duracao = str(reg[4]) if len(reg) > 4 else ""
                status = str(reg[5]) if len(reg) > 5 else ""
                tipo = str(reg[6]) if len(reg) > 6 else ""

                cursor.execute("""
                    INSERT OR IGNORE INTO todas_chamadas 
                    (data_hora, origem, destino, ramal_tecnico, duracao, status, tipo)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (data_hora, origem, destino, ramal_tecnico, duracao, status, tipo))
        except Exception:
            continue
    conn.commit()
    conn.close()

def carregar_do_banco(data_inicio, data_fim):
    """
    Carrega todos os dados do banco local e aplica o filtro de período.
    """
    conn = sqlite3.connect("cdr_nao_atendidas.db")
    query = "SELECT * FROM todas_chamadas"
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if not df.empty:
        df["data_obj"] = pd.to_datetime(df["data_hora"], format="%d-%m-%Y %H:%M:%S", errors="coerce")
        mask = (df["data_obj"].dt.date >= data_inicio) & (df["data_obj"].dt.date <= data_fim)
        df = df.loc[mask]
    return df

# ===== MENU LATERAL =====
st.sidebar.header("Navegação & Filtros")
menu = st.sidebar.radio("Escolha a Opção", ["Dashboard Geral", "🔍 Auditoria de Log por Telefone"])

data_inicio = st.sidebar.date_input("Data início")
data_fim = st.sidebar.date_input("Data fim")

# Botão de Sincronização Global com a API da Evence
if st.sidebar.button("Sincronizar Dados da API Evence"):
    indice = 0
    total_inseridos = 0
    sucesso_busca = False
    
    # Armazena a última resposta bruta para fins de inspeção/debug
    st.session_state["ultimo_json_bruto"] = {}
    
    with st.spinner("Buscando registros na API de CDR..."):
        while True:
            url = f"{BASE_URL}?api_token={API_TOKEN}&datainicio={data_inicio}&datafinal={data_fim}&indice={indice}"
            
            try:
                response = requests.get(url)
                if response.status_code == 404:
                    break
                if response.status_code != 200:
                    st.error(f"Erro na API: Status {response.status_code}")
                    break
                
                data = response.json()
                if "error" in data:
                    st.error(f"Erro retornado pela API: {data['error']}")
                    break
                
                # Guarda o JSON bruto na sessão para inspecionar depois
                st.session_state["ultimo_json_bruto"] = data
                
                cdr_dict = data.get("cdr", {})
                if not cdr_dict:
                    break 
                
                lista_registros = list(cdr_dict.values())
                salvar_no_banco(lista_registros)
                
                total_inseridos += len(lista_registros)
                indice += len(lista_registros) 
                sucesso_busca = True
                
                if len(lista_registros) == 0:
                    break
            except Exception as e:
                st.error(f"Erro de conexão: {e}")
                break
                
    if sucesso_busca or total_inseridos > 0:
        st.sidebar.success(f"Sincronização concluída! {total_inseridos} registros processados.")
    else:
        st.sidebar.warning("Nenhum registro retornado pela API para este período.")

# Seção de Debug na Barra Lateral para inspecjonar o log cru da API
with st.sidebar.expander("🛠️ Inspecionar Log Bruto da API"):
    if "ultimo_json_bruto" in st.session_state and st.session_state["ultimo_json_bruto"]:
        st.write("Estrutura exata recebida da Evence:")
        st.json(st.session_state["ultimo_json_bruto"])
    else:
        st.info("Clique em 'Sincronizar Dados da API Evence' para capturar o log bruto.")

# Carrega a base geral filtrada por período
df_geral = carregar_do_banco(data_inicio, data_fim)

# ==========================================
# OPÇÃO 1: DASHBOARD GERAL
# ==========================================
if menu == "Dashboard Geral":
    st.subheader(f"📊 Painel de Chamadas Não Atendidas ({data_inicio} a {data_fim})")
    
    if not df_geral.empty:
        df_nao_atendidas = df_geral[df_geral["status"].str.lower().str.contains("não atendida", na=False)]
        
        if not df_nao_atendidas.empty:
            contagem = df_nao_atendidas["ramal_tecnico"].value_counts().reset_index()
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

            st.subheader("Detalhamento Geral de Chamadas Perdidas")
            st.dataframe(df_nao_atendidas[["data_hora", "origem", "destino", "ramal_tecnico", "status", "tipo"]])
        else:
            st.info("Nenhuma chamada 'Não Atendida' registrada neste período.")
    else:
        st.info("Banco de dados vazio para este período. Clique em 'Sincronizar Dados da API Evence' na barra lateral.")

# ==========================================
# OPÇÃO 2: AUDITORIA DE LOG POR TELEFONE
# ==========================================
elif menu == "🔍 Auditoria de Log por Telefone":
    st.subheader("🔍 Rastreio e Auditoria de Chamada por Número de Cliente")
    st.markdown("Digite o número do telefone do cliente (ou parte dele) para mapear todas as tentativas, transbordos e ramais por onde a ligação passou.")
    
    telefone_busca = st.text_input("Número do Telefone do Cliente:", "")
    
    if telefone_busca:
        if not df_geral.empty:
            df_cliente = df_geral[
                df_geral["origem"].astype(str).str.contains(telefone_busca, na=False) | 
                df_geral["destino"].astype(str).str.contains(telefone_busca, na=False)
            ]
            
            if not df_cliente.empty:
                st.success(f"Encontrados {len(df_cliente)} registros de movimentação para o número: **{telefone_busca}**")
                
                df_cliente = df_cliente.sort_values(by="data_obj", ascending=True)
                
                st.markdown("### 🕒 Linha do Tempo Completa da Chamada")
                for idx, row in df_cliente.iterrows():
                    status_str = str(row["status"])
                    status_cor = "🔴" if "não atendida" in status_str.lower() else "🟢"
                    
                    st.markdown(f"""
                    * **{status_cor} Data/Hora:** `{row['data_hora']}`  
                      * **Origem:** `{row['origem']}` | **Destino:** `{row['destino']}`  
                      * **Ramal / Técnico:** `{row['ramal_tecnico']}`  
                      * **Status:** `{status_str}` | **Tipo:** `{row['tipo']}` | **Duração:** `{row['duracao']}`
                    """)
                
                st.markdown("---")
                st.subheader("Tabela Analítica Completa do Número")
                st.dataframe(df_cliente[["data_hora", "origem", "destino", "ramal_tecnico", "status", "tipo", "duracao"]])
                
            else:
                st.warning(f"Nenhum registro encontrado para o número '{telefone_busca}' no período selecionado.")
        else:
            st.warning("Carregue os dados sincronizando a API primeiro.")
