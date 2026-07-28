import streamlit as st
import requests
import plotly.express as px
from bs4 import BeautifulSoup
from collections import Counter
import sqlite3
import pandas as pd
import io

st.set_page_config(layout="wide")
st.title("📊 Relatório de Recusas por Técnico - Monitoramento Persistente")

# ===== CONFIGURAÇÃO DO BANCO DE DADOS LOCAL (SQLite) =====
def init_db():
    conn = sqlite3.connect("recusas_tecnicos.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS historico_recusas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_busca TEXT,
            fila_id TEXT,
            tecnico TEXT,
            quantidade INTEGER,
            UNIQUE(data_busca, fila_id, tecnico)
        )
    """)
    conn.commit()
    conn.close()

init_db()

def salvar_no_banco(data_busca, fila_id, contagem):
    conn = sqlite3.connect("recusas_tecnicos.db")
    cursor = conn.cursor()
    for tecnico, qtd in contagem.items():
        cursor.execute("""
            INSERT INTO historico_recusas (data_busca, fila_id, tecnico, quantidade)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(data_busca, fila_id, tecnico) 
            DO UPDATE SET quantidade = excluded.quantidade
        """, (data_busca, fila_id, tecnico, qtd))
    conn.commit()
    conn.close()

def carregar_do_banco(fila_id, data_inicio, data_fim):
    conn = sqlite3.connect("recusas_tecnicos.db")
    query = """
        SELECT tecnico, SUM(quantidade) as total 
        FROM historico_recusas 
        WHERE fila_id = ? AND data_busca BETWEEN ? AND ?
        GROUP BY tecnico
    """
    df = pd.read_sql_query(query, conn, params=(fila_id, str(data_inicio), str(data_fim)))
    conn.close()
    return df

# ===== CONFIG LOGIN =====
login_url = "https://pabx.evence.com.br/login"
email = "suporte@interativanet.com.br"
senha = "smk03657"

# ===== FUNÇÃO LOGIN =====
def login_pabx():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    try:
        r = session.get(login_url)
        soup = BeautifulSoup(r.text, "html.parser")
        csrf = soup.find("input", {"name": "_token"})
        if not csrf:
            st.error("Erro ao pegar token CSRF")
            return None

        payload = {"login": email, "senha": senha, "_token": csrf["value"]}
        response = session.post(login_url, data=payload)

        if response.url == login_url:
            st.error("Login falhou")
            return None
        return session
    except Exception as e:
        st.error(f"Erro no login: {e}")
        return None

# ===== CRIA SESSÃO =====
if "session_pabx" not in st.session_state:
    st.session_state.session_pabx = login_pabx()

# ===== FILTROS NA TELA =====
st.sidebar.header("Filtros")
fila_id = st.sidebar.text_input("Fila ID", "2812")
data_inicio = st.sidebar.date_input("Data início")
data_fim = st.sidebar.date_input("Data fim")

# ===== BOTÃO BUSCAR =====
if st.button("Buscar Dados da Evence e Atualizar"):
    session = st.session_state.session_pabx
    if not session:
        st.error("Sessão inválida")
        st.stop()

    d_ini_str = str(data_inicio)
    d_fim_str = str(data_fim)
    url = f"https://pabx.evence.com.br/callcenter/relatorios/recusa-pa?fila_id={fila_id}&data_inicial={d_ini_str}&data_final={d_fim_str}"

    response = session.get(url)
    if "login" in response.url:
        session = login_pabx()
        st.session_state.session_pabx = session
        response = session.get(url)

    if response.status_code != 200:
        st.error("Erro ao acessar relatório")
        st.stop()

    soup = BeautifulSoup(response.text, "html.parser")
    tecnicos = []

    # Paginação
    ultima_pagina = 1
    paginacao = soup.find("ul", class_="pagination")
    if paginacao:
        paginas = paginacao.find_all("a")
        numeros = []
        for p in paginas:
            try:
                numeros.append(int(p.text.strip()))
            except:
                pass
        if numeros:
            ultima_pagina = max(numeros)

    # Loop de páginas
    for page in range(1, ultima_pagina + 1):
        url_pagina = f"{url}&page={page}"
        response = session.get(url_pagina)
        soup = BeautifulSoup(response.text, "html.parser")
        tabela = soup.find("table")
        if not tabela:
            continue
        linhas = tabela.find("tbody").find_all("tr")
        for linha in linhas:
            colunas = linha.find_all("td")
            if len(colunas) >= 3:
                tecnico = colunas[2].text.strip()
                tecnicos.append(tecnico)

    contagem = dict(Counter(tecnicos))
    
    # Salva no banco de dados local por data de busca para manter histórico
    if contagem:
        salvar_no_banco(str(data_inicio), fila_id, contagem)
        st.success("Dados buscados e salvos com sucesso no banco de dados local!")
    else:
        st.warning("Nenhum registro encontrado para o período.")

# ===== EXIBIÇÃO DOS DADOS SALVOS (Persistidos) =====
df_resultado = carregar_do_banco(fila_id, data_inicio, data_fim)

if not df_resultado.empty:
    st.subheader(f"Resumo por Técnico (Período: {data_inicio} a {data_fim})")

    # Cards métricas
    cols = st.columns(4)
    for i, row in df_resultado.iterrows():
        cols[i % 4].metric(row["tecnico"], int(row["total"]))

    # Gráfico
    fig = px.pie(
        df_resultado,
        names="tecnico",
        values="total",
        title="Proporção de Recusas"
    )
    st.plotly_chart(fig, use_container_width=True)

    # ===== BOTÕES DE EXPORTAÇÃO =====
    st.markdown("---")
    st.subheader("Exportar Relatórios")
    
    col_exp1, col_exp2 = st.columns(2)

    # 1. Exportar para Excel (XLSX)
    with col_exp1:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_resultado.to_excel(writer, index=False, sheet_name='Recusas')
        excel_data = output.getvalue()
        
        st.download_button(
            label="📥 Baixar Relatório em Excel (XLSX)",
            data=excel_data,
            file_name=f"relatorio_recusas_{data_inicio}_a_{data_fim}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    # 2. Exportar para PDF Simples (via FPDF2)
    with col_exp2:
        from fpdf import FPDF

    def gerar_pdf(df):
            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Arial", "B", 16)
            pdf.cell(200, 10, txt="Relatorio de Recusas por Tecnico", ln=True, align="C")
            pdf.set_font("Arial", "", 12)
            pdf.cell(200, 10, txt="Desempenho no periodo indicado", ln=True, align="C") # <-- Adicionadas as aspas aqui
            pdf.ln(10)
            
            # Cabeçalho da tabela
            pdf.set_font("Arial", "B", 12)
            pdf.cell(130, 10, "Tecnico", 1)
            pdf.cell(60, 10, "Quantidade", 1, ln=True)
            
            # Linhas
            pdf.set_font("Arial", "", 12)
            for _, row in df.iterrows():
                pdf.cell(130, 10, str(row["tecnico"]), 1)
                pdf.cell(60, 10, str(int(row["total"])), 1, ln=True)
                
            return pdf.output()
        
        try:
            pdf_bytes = bytes(gerar_pdf(df_resultado))
            st.download_button(
                label="📥 Baixar Relatório em PDF",
                data=pdf_bytes,
                file_name=f"relatorio_recusas_{data_inicio}_a_{data_fim}.pdf",
                mime="application/pdf"
            )
        except Exception as e:
            st.info("Para habilitar o PDF, certifique-se de ter o 'fpdf2' instalado.")

else:
    st.info("Nenhum dado encontrado no banco de dados local para este filtro. Clique em 'Buscar Dados' para carregar da Evence.")
