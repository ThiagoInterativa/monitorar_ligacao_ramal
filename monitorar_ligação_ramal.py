import streamlit as st
import pandas as pd
import sqlite3
from datetime import date

st.set_page_config(layout="wide")
st.title("📊 Sistema Unificado de Auditoria de Chamadas (Evence)")

# Banco de dados centralizado
def init_db():
    conn = sqlite3.connect("auditoria_unificada.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS historico_unificado (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_hora TEXT,
            origem TEXT,
            destino TEXT,
            status TEXT,
            tipo_origem TEXT,
            detalhes TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ===== MENU LATERAL E FILTROS =====
st.sidebar.header("⚙️ Configuração & Filtros")
menu = st.sidebar.radio("Navegação", ["🔍 Pesquisa Unificada por Telefone", "📥 Importar / Alimentar Dados"])

st.sidebar.markdown("---")
st.sidebar.subheader("Filtros de Período")
data_inicio = st.sidebar.date_input("Data Inicial", date(2026, 7, 1))
data_fim = st.sidebar.date_input("Data Final", date(2026, 7, 31))

telefone_busca = st.sidebar.text_input("Número do Cliente (Filtro)", "1143820682")

# ==========================================
# OPÇÃO 1: PESQUISA UNIFICADA
# ==========================================
if menu == "🔍 Pesquisa Unificada por Telefone":
    st.subheader(f"🔍 Auditoria Cruzada para o Telefone: `{telefone_busca}` ({data_inicio} até {data_fim})")
    st.markdown("O sistema cruza as informações de CDR Sintético, Recusas na P.A. e Fila para montar a linha do tempo exata.")

    if telefone_busca:
        conn = sqlite3.connect("auditoria_unificada.db")
        # Busca básica por telefone na tabela
        query = f"SELECT * FROM historico_unificado WHERE (origem LIKE '%{telefone_busca}%' OR destino LIKE '%{telefone_busca}%' OR detalhes LIKE '%{telefone_busca}%')"
        df_resultado = pd.read_sql_query(query, conn)
        conn.close()

        if not df_resultado.empty:
            # Converte a data para objeto datetime para filtrar corretamente por período
            df_resultado["data_obj"] = pd.to_datetime(df_resultado["data_hora"], format="%d-%m-%Y %H:%M:%S", errors="coerce")
            
            # Aplica o filtro de data inicial e final
            mask = (df_resultado["data_obj"].dt.date >= data_inicio) & (df_resultado["data_obj"].dt.date <= data_fim)
            df_filtrado = df_resultado.loc[mask].sort_values(by="data_obj", ascending=True)

            if not df_filtrado.empty:
                st.success(f"Encontrados {len(df_filtrado)} eventos consolidados para este número no período selecionado.")
                
                st.markdown("### 🗺️ Linha do Tempo Unificada (Da Chamada à Resposta)")
                
                for idx, row in df_filtrado.iterrows():
                    status = str(row["status"]).lower()
                    
                    if "atendida" in status:
                        icone = "🟢 [ATENDIDA]"
                    elif "abandonada" in status:
                        icone = "🔴 [ABANDONADA / DESISTIU]"
                    elif "recusada" in status or "intercom" in status:
                        icone = "🟠 [RECUSADA NA P.A. / TRANSFERIDA]"
                    else:
                        icone = "🔵 [EVENTO PABX]"

                    st.markdown(f"""
                    ---
                    #### {icone} — `{row['data_hora']}`
                    * **Origem / Cliente:** `{row['origem']}`
                    * **Destino / Ramal / Atendente:** `{row['destino']}`
                    * **Status da Ocorrência:** `{row['status']}`
                    * **Origem do Log:** `{row['tipo_origem']}`
                    * *Detalhes:* `{row['detalhes']}`
                    """)
                
                st.markdown("---")
                st.subheader("Tabela Analítica Completa")
                st.dataframe(df_filtrado[["data_hora", "origem", "destino", "status", "tipo_origem", "detalhes"]])
            else:
                st.warning("Existem registros para este número, mas nenhum dentro do intervalo de datas selecionado na barra lateral.")
        else:
            st.warning("Nenhum registro unificado encontrado para este número. Insira os dados na aba de importação ou verifique o número.")
    else:
        st.info("Digite um número de telefone na barra lateral para iniciar a pesquisa.")

# ==========================================
# OPÇÃO 2: IMPORTAR / ALIMENTAR DADOS
# ==========================================
elif menu == "📥 Importar / Alimentar Dados":
    st.subheader("📥 Central de Ingestão de Dados para Auditoria")
    st.markdown("Alimente os registros coletados dos relatórios web (`/cdr/pesquisar` e `recusa-pa`) para que o sistema cruze as informações.")

    with st.form("form_insercao"):
        st.write("Adicionar Evento Manual / Coletado")
        f_data = st.text_input("Data e Hora (Formato: DD-MM-YYYY HH:MM:SS)", "28-07-2026 14:10:16")
        f_origem = st.text_input("Origem (Número do Cliente)", "1143820682")
        f_destino = st.text_input("Destino (Ramal ou Fila, ex: Gabriel Tomaz < 108 >)", "Gabriel Tomaz < 108 >")
        f_status = st.selectbox("Status", ["Atendida", "Recusada na P.A.", "Abandonada", "Chamada Intercom"])
        f_tipo = st.selectbox("Fonte do Relatório", ["CDR Sintético", "Relatório de Recusa P.A.", "Fila Realtime"])
        f_detalhes = st.text_area("Detalhes Adicionais (Ex: Duração, Agente envolvido, Transbordo)", "Duração 00:17:25 - Transferido para Vinícius")
        
        submitted = st.form_submit_button("Salvar na Auditoria Unificada")
        if submitted:
            conn = sqlite3.connect("auditoria_unificada.db")
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO historico_unificado (data_hora, origem, destino, status, tipo_origem, detalhes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (f_data, f_origem, f_destino, f_status, f_tipo, f_detalhes))
            conn.commit()
            conn.close()
            st.success("Evento adicionado com sucesso à auditoria!")
