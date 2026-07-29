import streamlit as st
import requests
import plotly.express as px
import sqlite3
import pandas as pd
import re

# Configuração inicial da página do Streamlit para largura total
st.set_page_config(layout="wide")
st.title("📊 Monitoramento e Auditoria Avançada de Chamadas (API CDR Evence)")

# ===== CONFIGURAÇÃO DA API E BANCO =====
API_TOKEN = "4275c3fd79ac7997e3dc03fb451657518b50d55203c41c8798a3c81eb5825031"
BASE_URL = "https://pabx.evence.com.br/api/v1/cdr"

def extrair_tecnico_forca_bruta(item_bruto):
    """
    Varre qualquer estrutura (lista, dicionário ou string) em busca do padrão Asterisk: Nome <ramal>
    """
    if isinstance(item_bruto, (list, tuple)):
        texto_unificado = " ".join([str(x) for x in item_bruto])
    elif isinstance(item_bruto, dict):
        texto_unificado = " ".join([str(v) for v in item_bruto.values()])
    else:
        texto_unificado = str(item_bruto)

    # Procura por padrões como "Gabriel Tomaz < 108 >" ou 'Gabriel Tomaz' <108>
    match = re.search(r'"?([^"<]+)"?\s*<\s*(\d+)\s*>', texto_unificado)
    if match:
        nome = match.group(1).strip().replace('"', '')
        ramal = match.group(2).strip()
        return f"{nome} (Ramal {ramal})"
    return ""

def init_db():
    conn = sqlite3.connect("cdr_nao_atendidas.db")
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS todas_chamadas")
    cursor.execute("""
        CREATE TABLE todas_chamadas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_hora TEXT,
            origem TEXT,
            destino TEXT,
            canal_ramal TEXT,
            duracao TEXT,
            status TEXT,
            tipo TEXT,
            tecnico_formatado TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def salvar_no_banco(registros):
    conn = sqlite3.connect("cdr_nao_atendidas.db")
    cursor = conn.cursor()
    
    # Se a API retornar um dicionário ou lista de registros
    if isinstance(registros, dict):
        itens = registros.values()
    else:
        itens = registros

    for reg in itens:
        try:
            # Garante que conseguimos extrair o técnico de qualquer formato que a API mande (lista ou string)
            tecnico_encontrado = extrair_tecnico_forca_bruta(reg)
            
            if isinstance(reg, (list, tuple)) and len(reg) >= 6:
                data_hora = str(reg[0]) if len(reg) > 0 else ""
                origem = str(reg[1]) if len(reg) > 1 else ""
                destino = str(reg[2]) if len(reg) > 2 else ""
                canal_ramal = str(reg[3]) if len(reg) > 3 else ""
                duracao = str(reg[4]) if len(reg) > 4 else ""
                status = str(reg[5]) if len(reg) > 5 else ""
                tipo = str(reg[6]) if len(reg) > 6 else "Desconhecido"
            else:
                # Fallback caso venha em formato de texto bruto ou dicionário alternativo
                data_hora = ""
                origem = str(reg)
                destino = ""
                canal_ramal = ""
                duracao = ""
                status = ""
                tipo = "Desconhecido"

            if not tecnico_encontrado:
                tecnico_encontrado = "Fila de Atendimento Geral"

            cursor.execute("""
                INSERT INTO todas_chamadas 
                (data_hora, origem, destino, canal_ramal, duracao, status, tipo, tecnico_formatado)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (data_hora, origem, destino, canal_ramal, duracao, status, tipo, tecnico_encontrado))
        except Exception:
            continue
            
    conn.commit()
    conn.close()

def carregar_do_banco(data_inicio, data_fim):
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

if st.sidebar.button("Sincronizar Dados da API Evence"):
    indice = 0
    total_inseridos = 0
    sucesso_busca = False
    st.session_state["ultimo_json_bruto"] = {}
    
    with st.spinner("Buscando registros na API de CDR..."):
        while True:
            url = f"{BASE_URL}?api_token={API_TOKEN}&datainicio={data_inicio}&datafinal={data_fim}&indice={indice}"
            try:
                response = requests.get(url)
                if response.status_code != 200:
                    break
                data = response.json()
                if "error" in data:
                    break
                
                st.session_state["ultimo_json_bruto"] = data
                cdr_dict = data.get("cdr", {})
                if not cdr_dict:
                    break 
                
                salvar_no_banco(cdr_dict)
                
                qtd = len(cdr_dict)
                total_inseridos += qtd
                indice += qtd 
                sucesso_busca = True
                
                if qtd == 0:
                    break
            except Exception:
                break
                
    if sucesso_busca or total_inseridos > 0:
        st.sidebar.success(f"Sincronização concluída! {total_inseridos} registros processados.")
    else:
        st.sidebar.warning("Nenhum registro retornado pela API para este período.")

with st.sidebar.expander("🛠️ Inspecionar Log Bruto da API"):
    if "ultimo_json_bruto" in st.session_state and st.session_state["ultimo_json_bruto"]:
        st.json(st.session_state["ultimo_json_bruto"])
    else:
        st.info("Clique em 'Sincronizar Dados da API Evence' para capturar o log bruto.")

df_geral = carregar_do_banco(data_inicio, data_fim)

# ==========================================
# OPÇÃO 1: DASHBOARD GERAL
# ==========================================
if menu == "Dashboard Geral":
    st.subheader(f"📊 Painel de Chamadas ({data_inicio} a {data_fim})")
    
    if not df_geral.empty:
        df_abandonadas = df_geral[df_geral["status"].str.lower().str.contains("abandonada", na=False)]
        
        if not df_abandonadas.empty:
            contagem = df_abandonadas["tecnico_formatado"].value_counts().reset_index()
            contagem.columns = ["Técnico / Origem", "Quantidade"]

            cols = st.columns(4)
            for i, row in contagem.head(4).iterrows():
                cols[i % 4].metric(str(row['Técnico / Origem']), int(row['Quantidade']))

            fig = px.pie(
                contagem,
                names="Técnico / Origem",
                values="Quantidade",
                title="Proporção de Chamadas Abandonadas"
            )
            st.plotly_chart(fig, use_container_width=True)

            st.subheader("Detalhamento Geral")
            st.dataframe(df_abandonadas[["data_hora", "origem", "destino", "tecnico_formatado", "status", "tipo"]])
        else:
            st.info("Nenhuma chamada abandonada registrada neste período.")
    else:
        st.info("Banco de dados vazio para este período. Sincronize os dados.")

# ==========================================
# OPÇÃO 2: AUDITORIA DE LOG POR TELEFONE
# ==========================================
elif menu == "🔍 Auditoria de Log por Telefone":
    st.subheader("🔍 Rastreio e Auditoria de Chamada por Número de Cliente")
    st.markdown("Digite o número do telefone do cliente para rastrear por onde passou a ligação.")
    
    telefone_busca = st.text_input("Número do Telefone do Cliente:", "")
    
    if telefone_busca:
        if not df_geral.empty:
            df_cliente = df_geral[
                df_geral["origem"].astype(str).str.contains(telefone_busca, na=False) | 
                df_geral["destino"].astype(str).str.contains(telefone_busca, na=False) |
                df_geral["canal_ramal"].astype(str).str.contains(telefone_busca, na=False)
            ]
            
            if not df_cliente.empty:
                st.success(f"Encontrados {len(df_cliente)} eventos de PABX para o número: **{telefone_busca}**")
                df_cliente = df_cliente.sort_values(by="data_obj", ascending=True)
                
                st.markdown("### 🕒 Linha do Tempo e Trilha de Auditoria")
                for idx, row in df_cliente.iterrows():
                    status_str = str(row["status"])
                    status_cor = "🔴" if "abandonada" in status_str.lower() else "🟢"
                    
                    st.markdown(f"""
                    * **{status_cor} Data/Hora:** `{row['data_hora']}`  
                      * **Origem (Bina):** `{row['origem']}`  
                      * **Destino / Fila:** `{row['destino']}`  
                      * **Atendente / Técnico Envolvido:** `⭐ {row['tecnico_formatado']}`  
                      * **Status:** `{status_str}` | **Tipo da Chamada:** `{row['tipo']}` | **Duração:** `{row['duracao']}`
                    """)
                
                st.markdown("---")
                st.subheader("Tabela Analítica Completa para Auditoria")
                st.dataframe(df_cliente[["data_hora", "origem", "destino", "canal_ramal", "tecnico_formatado", "status", "tipo", "duracao"]])
            else:
                st.warning(f"Nenhum registro encontrado para '{telefone_busca}'. Verifique se o período das datas abrange esse dia.")
        else:
            st.warning("Sincronize a API primeiro.")
