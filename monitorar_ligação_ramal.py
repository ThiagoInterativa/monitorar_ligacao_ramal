import streamlit as st
import requests
import plotly.express as px
import sqlite3
import pandas as pd
import re
from datetime import datetime, timedelta

st.set_page_config(layout="wide")
st.title("📊 Auditoria de Rota Completa de Chamadas (Cruzamento PABX Evence)")

API_TOKEN = "4275c3fd79ac7997e3dc03fb451657518b50d55203c41c8798a3c81eb5825031"
BASE_URL = "https://pabx.evence.com.br/api/v1/cdr"

def init_db():
    conn = sqlite3.connect("cdr_auditoria_rota.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trilha_chamadas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_hora TEXT,
            origem TEXT,
            destino TEXT,
            canal_ramal TEXT,
            duracao TEXT,
            status TEXT,
            tipo TEXT,
            detalhes_brutos TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def limpar_banco():
    conn = sqlite3.connect("cdr_auditoria_rota.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM trilha_chamadas")
    conn.commit()
    conn.close()

def salvar_no_banco(registros):
    conn = sqlite3.connect("cdr_auditoria_rota.db")
    cursor = conn.cursor()
    
    itens = registros.values() if isinstance(registros, dict) else registros

    for reg in itens:
        try:
            if isinstance(reg, (list, tuple)) and len(reg) >= 6:
                data_hora = str(reg[0]) if len(reg) > 0 else ""
                origem = str(reg[1]) if len(reg) > 1 else ""
                destino = str(reg[2]) if len(reg) > 2 else ""
                canal_ramal = str(reg[3]) if len(reg) > 3 else ""
                duracao = str(reg[4]) if len(reg) > 4 else ""
                status = str(reg[5]) if len(reg) > 5 else ""
                tipo = str(reg[6]) if len(reg) > 6 else "Desconhecido"
                detalhes_brutos = str(reg)
            else:
                data_hora = ""
                origem = str(reg)
                destino = ""
                canal_ramal = ""
                duracao = ""
                status = ""
                tipo = "Desconhecido"
                detalhes_brutos = str(reg)

            cursor.execute("""
                INSERT INTO trilha_chamadas 
                (data_hora, origem, destino, canal_ramal, duracao, status, tipo, detalhes_brutos)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (data_hora, origem, destino, canal_ramal, duracao, status, tipo, detalhes_brutos))
        except Exception:
            continue
            
    conn.commit()
    conn.close()

def carregar_do_banco(data_inicio, data_fim):
    conn = sqlite3.connect("cdr_auditoria_rota.db")
    df = pd.read_sql_query("SELECT * FROM trilha_chamadas", conn)
    conn.close()
    
    if not df.empty:
        df["data_obj"] = pd.to_datetime(df["data_hora"], format="%d-%m-%Y %H:%M:%S", errors="coerce")
        mask = (df["data_obj"].dt.date >= data_inicio) & (df["data_obj"].dt.date <= data_fim)
        df = df.loc[mask]
    return df

# ===== MENU LATERAL =====
st.sidebar.header("Navegação & Filtros")
menu = st.sidebar.radio("Opções", ["🔍 Auditoria de Rota por Telefone", "📊 Resumo Geral"])

data_inicio = st.sidebar.date_input("Data início")
data_fim = st.sidebar.date_input("Data fim")

if st.sidebar.button("🔄 Sincronizar Dados da API Evence"):
    limpar_banco()
    indice = 0
    total_inseridos = 0
    
    with st.spinner("Sincronizando logs completos do PABX (Isso pode levar alguns segundos)..."):
        while True:
            url = f"{BASE_URL}?api_token={API_TOKEN}&datainicio={data_inicio}&datafinal={data_fim}&indice={indice}"
            try:
                response = requests.get(url)
                if response.status_code != 200:
                    break
                data = response.json()
                if "error" in data:
                    break
                
                # A Evence costuma retornar os dados dentro da chave 'cdr' ou como uma lista direta
                cdr_dict = data.get("cdr", data)
                if not cdr_dict or len(cdr_dict) == 0:
                    break 
                
                salvar_no_banco(cdr_dict)
                qtd = len(cdr_dict)
                total_inseridos += qtd
                
                # Se o retorno for menor que o lote padrão ou se repetir o índice, evita loop infinito
                if qtd == 0:
                    break
                
                indice += qtd 
                
                # Segurança extra: se a API retornar menos de 1 item novo, encerra para evitar travamento
                if isinstance(cdr_dict, dict) and len(cdr_dict) == 0:
                    break
            except Exception as e:
                st.sidebar.error(f"Erro na sincronização: {e}")
                break
                
    st.sidebar.success(f"Sincronização concluída! Total de {total_inseridos} eventos carregados.")
    
df_geral = carregar_do_banco(data_inicio, data_fim)

# ==========================================
# AUDITORIA DE ROTA POR TELEFONE
# ==========================================
# ==========================================
# OPÇÃO 2: AUDITORIA DE ROTA POR TELEFONE
# ==========================================
if menu == "🔍 Auditoria de Log por Telefone":
    st.subheader("🔍 Rastreio e Trilha Completa da Chamada")
    st.markdown("Digite o número do telefone do cliente para rastrear todos os eventos próximos e a rota que a ligação fez.")
    
    telefone_busca = st.text_input("Número do Telefone do Cliente (Ex: 1143820682):", "")
    
    if telefone_busca:
        if not df_geral.empty:
            # 1. Encontra chamadas que contêm o número do cliente
            df_cliente = df_geral[
                df_geral["origem"].astype(str).str.contains(telefone_busca, na=False) | 
                df_geral["destino"].astype(str).str.contains(telefone_busca, na=False) |
                df_geral["canal_ramal"].astype(str).str.contains(telefone_busca, na=False) |
                df_geral["detalhes_brutos"].astype(str).str.contains(telefone_busca, na=False)
            ]
            
            if not df_cliente.empty:
                st.success(f"Encontrados **{len(df_cliente)}** eventos diretos para o número **{telefone_busca}**")
                
                # 2. JANELA DE AUDITORIA EXPANDIDA: Pega também os eventos que ocorreram no mesmo dia/horário (para achar ramais, intercom e filas próximas)
                primeira_data = df_cliente["data_obj"].min()
                ultima_data = df_cliente["data_obj"].max()
                
                if pd.notnull(primeira_data) and pd.notnull(ultima_data):
                    # Expande a janela em 10 minutos antes e depois para capturar o contexto da rota
                    janela_inicio = primeira_data - pd.Timedelta(minutes=5)
                    janela_fim = ultima_data + pd.Timedelta(minutes=30)
                    
                    df_rota_completa = df_geral[
                        (df_geral["data_obj"] >= janela_inicio) & 
                        (df_geral["data_obj"] <= janela_fim)
                    ].sort_values(by="data_obj", ascending=True)
                else:
                    df_rota_completa = df_cliente

                st.markdown("### 🗺️ Rota Cronológica Contextualizada (O que aconteceu ao redor?)")
                st.info("Mostrando eventos do PABX na mesma janela de horário para identificar ramais, transferências e interações da equipe.")
                
                for idx, row in df_rota_completa.iterrows():
                    status = str(row["status"]).lower()
                    is_alvo = telefone_busca in str(row["detalhes_brutos"])
                    
                    # Destaque visual se o evento pertencer diretamente ao cliente ou ao contexto
                    prefixo_destaque = "⭐ **[CHAMADA DO CLIENTE]**" if is_alvo else "🔄 **[Evento de Contexto / Ramal / Fila]**"
                    
                    if "atendida" in status:
                        icone = "🟢"
                    elif "abandonada" in status:
                        icone = "🔴"
                    else:
                        icone = "🟠"
                    
                    st.markdown(f"""
                    ---
                    {prefixo_destaque}  
                    * **{icone} Data/Hora:** `{row['data_hora']}`  
                    * **Origem:** `{row['origem']}` | **Destino:** `{row['destino']}`  
                    * **Canal/Ramal:** `{row['canal_ramal']}` | **Status:** `{row['status']}` | **Tipo:** `{row['tipo']}` | **Duração:** `{row['duracao']}`  
                    * *Dados Brutos:* `{row['detalhes_brutos']}`
                    """)
                
                st.markdown("---")
                st.subheader("Tabela Analítica da Janela da Chamada")
                st.dataframe(df_rota_completa[["data_hora", "origem", "destino", "canal_ramal", "status", "tipo", "duracao"]])
            else:
                st.warning(f"Nenhum evento direto encontrado para '{telefone_busca}'. Verifique se sincronizou a data correta.")
        else:
            st.warning("O banco de dados está vazio. Sincronize os dados da API primeiro.")
            
elif menu == "📊 Resumo Geral":
    st.subheader("📊 Visão Geral do Sistema")
    if not df_geral.empty:
        st.metric("Total de Eventos Registrados", len(df_geral))
        st.dataframe(df_geral.head(100))
    else:
        st.info("Sincronize os dados para visualizar.")
