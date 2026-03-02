import streamlit as st
import pandas as pd
import gspread
from datetime import datetime, timedelta
import time
import pytz
import os

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(page_title="Esteira Qualitor", page_icon="🎫", layout="wide")

# --- 👑 ADMINISTRAÇÃO ---
ADMINS = ["Eduardo", "EduardoSouza", "Gestor"] 

# --- CONEXÃO BLINDADA (ANTI-TRAVAMENTO DO GOOGLE) ---
@st.cache_resource
def conectar_e_abrir_abas():
    try:
        # 1. Verifica Credenciais
        if os.path.exists("credentials.json"):
            client = gspread.service_account(filename="credentials.json")
        else:
            try:
                creds_dict = st.secrets["gcp_service_account"]
                client = gspread.service_account_from_dict(creds_dict)
            except:
                return "🚨 ERRO: Credenciais não encontradas. Faltam os Secrets ou o credentials.json.", None, None, None, None
        
        # 2. Loop de Paciência (Tenta até 10 vezes se o Google travar)
        erro_real = ""
        for tentativa in range(10):
            try:
                sh = client.open("Chamados_Qualitor") # NOME OFICIAL DE PRODUÇÃO
                abas = sh.worksheets()
                
                if len(abas) >= 2:
                    aba_chamados = abas[0] if len(abas) > 0 else None
                    aba_users = abas[1] if len(abas) > 1 else None
                    aba_logs = abas[2] if len(abas) > 2 else None
                    aba_transp = abas[3] if len(abas) > 3 else None
                    return sh, aba_chamados, aba_users, aba_logs, aba_transp
                else:
                    erro_real = "A planilha tem menos de 2 abas visíveis."
            except Exception as e:
                erro_real = str(e)
                time.sleep(2 + tentativa) # Espera progressiva antes de tentar de novo
                
        return f"Falha após 10 tentativas. Último erro: {erro_real}", None, None, None, None
    except Exception as e:
        return f"Erro Crítico: {str(e)}", None, None, None, None

# --- FUNÇÕES DE TEMPO E LÓGICA ---
def hora_brasil():
    fuso = pytz.timezone('America/Sao_Paulo')
    return datetime.now(fuso) 

def hora_texto():
    return hora_brasil().strftime("%d/%m/%Y %H:%M:%S")

def data_hoje():
    return hora_brasil().strftime("%d/%m/%Y")

def calcular_minutos(data_inicio_str):
    try:
        inicio = datetime.strptime(data_inicio_str, "%d/%m/%Y %H:%M:%S")
        inicio = inicio.replace(tzinfo=pytz.timezone('America/Sao_Paulo'))
        agora = hora_brasil()
        diferenca = agora - inicio
        return int(diferenca.total_seconds() / 60)
    except: return 0

def formatar_tempo(minutos):
    horas, resto = divmod(minutos, 60)
    return f"{horas}h {resto}min"

def calcular_duracao_str(inicio_str, fim_str):
    try:
        fmt = "%d/%m/%Y %H:%M:%S"
        inicio = datetime.strptime(str(inicio_str), fmt)
        fim = datetime.strptime(str(fim_str), fmt)
        minutos = int((fim - inicio).total_seconds() / 60)
        if minutos < 0: return "0m"
        horas, resto = divmod(minutos, 60)
        if horas > 0: return f"{horas}h {resto}m"
        return f"{resto}m"
    except: return "-"

def minutos_gastos(inicio_str, fim_str):
    try:
        fmt = "%d/%m/%Y %H:%M:%S"
        i = datetime.strptime(str(inicio_str), fmt)
        f = datetime.strptime(str(fim_str), fmt)
        return (f - i).total_seconds() / 60
    except: return 0

# --- INICIAR CONEXÃO (COM DIAGNÓSTICO DETETIVE) ---
sh, aba_chamados, aba_users, aba_logs, aba_transp = conectar_e_abrir_abas()

if isinstance(sh, str):
    st.error("❌ A conexão falhou antes de carregar as abas.")
    st.warning(f"Diagnóstico: {sh}")
    if "SpreadsheetNotFound" in sh:
        st.info("💡 Dica: O robô logou no Google, mas não achou a planilha. Verifique se o nome está exatamente 'Chamados_Qualitor' e se o e-mail do robô está como Editor.")
    if st.button("Tentar conectar novamente agora"): st.rerun()
    st.stop()
elif aba_chamados is None or aba_users is None:
    st.error("Erro desconhecido ao tentar carregar as abas principais.")
    st.stop()

# --- FUNÇÕES DE DADOS (COM CACHE) ---
def registrar_log(usuario, acao):
    try: aba_logs.append_row([usuario, acao, hora_texto()])
    except: pass

@st.cache_data(ttl=15)
def carregar_dados_chamados():
    try:
        df = pd.DataFrame(aba_chamados.get_all_records())
        if 'Dados' in df.columns:
            df['Dados'] = df['Dados'].astype(str).str.replace(r'\.0$', '', regex=True)
        return df
    except: return pd.DataFrame()

@st.cache_data(ttl=30)
def carregar_status_equipe():
    try:
        dados = aba_users.get_all_values()
        if not dados: return pd.DataFrame()
        df = pd.DataFrame(dados[1:], columns=dados[0])
        df = df.loc[:, df.columns != ''] 
        return df
    except Exception as e:
        return pd.DataFrame()

@st.cache_data(ttl=3600) 
def carregar_agenda_transp():
    if aba_transp is None: return pd.DataFrame()
    try: return pd.DataFrame(aba_transp.get_all_records())
    except: return pd.DataFrame()

# EFEITO BALÕES
if 'soltar_baloes' in st.session_state and st.session_state['soltar_baloes']:
    st.balloons()
    st.session_state['soltar_baloes'] = False

# ===================================================
# 🎨 APLICADOR DE TEMAS
# ===================================================
if 'tema_escolhido' not in st.session_state:
    st.session_state['tema_escolhido'] = "Padrão"

if st.session_state['tema_escolhido'] == "Matrix":
    st.markdown("""
        <style>
        .stApp { background-color: #0D0D0D; }
        h1, h2, h3, h4, p, span, div, label { color: #00FF41 !important; font-family: 'Courier New', Courier, monospace !important; }
        .stButton>button { background-color: #000000; color: #00FF41; border: 1px solid #00FF41; box-shadow: 0 0 5px #00FF41;}
        .stButton>button:hover { background-color: #00FF41; color: #000000; }
        [data-testid="stSidebar"] { background-color: #050505; border-right: 2px solid #00FF41; }
        .stSelectbox>div>div { background-color: #000; color: #00FF41; border: 1px solid #00FF41; }
        .stTextInput>div>div>input { background-color: #000; color: #00FF41; border: 1px solid #00FF41; }
        </style>
    """, unsafe_allow_html=True)
elif st.session_state['tema_escolhido'] == "Dark":
    st.markdown("""
        <style>
        .stApp { background-color: #0b1120; }
        h1, h2, h3, h4, p, span, div, label { color: #e2e8f0 !important; }
        .stButton>button { background-color: #1e293b; color: #38bdf8; border: 1px solid #38bdf8; }
        .stButton>button:hover { background-color: #38bdf8; color: #0b1120; }
        [data-testid="stSidebar"] { background-color: #0f172a; border-right: 1px solid #1e293b; }
        </style>
    """, unsafe_allow_html=True)
elif st.session_state['tema_escolhido'] == "Rosa":
    st.markdown("""
        <style>
        .stApp { background-color: #fff0f5; }
        h1, h2, h3, h4, p, span, div, label { color: #d81b60 !important; font-family: 'Trebuchet MS', sans-serif !important; }
        .stButton>button { background-color: #ffb6c1; color: #fff; border: 2px solid #ff69b4; border-radius: 20px;}
        .stButton>button:hover { background-color: #ff69b4; color: #fff; }
        [data-testid="stSidebar"] { background-color: #ffe4e1; border-right: 2px solid #ffb6c1; }
        .stSelectbox>div>div { background-color: #fff; color: #d81b60; border: 2px solid #ffb6c1; border-radius: 15px;}
        .stTextInput>div>div>input { background-color: #fff; color: #d81b60; border: 2px solid #ffb6c1; border-radius: 15px;}
        </style>
    """, unsafe_allow_html=True)

# --- TELA DE LOGIN ---
if 'usuario' not in st.session_state:
    st.title("🎫 ESTEIRA - QUALITOR")
    
    df_equipe = carregar_status_equipe()
    if not df_equipe.empty and 'Colaboradores' in df_equipe.columns:
        lista_nomes = [n for n in df_equipe['Colaboradores'].tolist() if str(n).strip() != '']
        senhas = dict(zip(df_equipe['Colaboradores'], df_equipe.get('Senha', ['']*len(df_equipe))))
    else:
        lista_nomes = []; senhas = {}
        st.warning("⚠️ Planilha carregando ou aba Colaboradores vazia. Clique abaixo se demorar.")
        if st.button("🔄 Recarregar Nomes"):
            st.cache_data.clear(); st.rerun()
    
    c1, c2 = st.columns(2)
    escolha = c1.selectbox("Usuário:", [""] + lista_nomes)
    senha = c2.text_input("Senha:", type="password")
    
    if st.button("Entrar no Sistema"):
        if escolha and str(senha) == str(senhas.get(escolha, "")):
            st.session_state['usuario'] = escolha
            st.session_state['tamanho_fila_anterior'] = 0 
            registrar_log(escolha, "LOGIN") 
            
            try:
                idx = df_equipe.index[df_equipe['Colaboradores'] == escolha].tolist()[0] + 2
                aba_users.update_cell(idx, 3, "Disponivel")
                st.cache_data.clear() 
            except: pass
            
            st.rerun()
        else: st.error("Dados inválidos.")

# --- SISTEMA LOGADO ---
else:
    usuario = st.session_state['usuario']
    df = carregar_dados_chamados()
    df_equipe = carregar_status_equipe()
    
    # 🎯 GPS DE COLUNAS
    if not df.empty:
        cols_planilha = df.columns.tolist()
        COL_STATUS = cols_planilha.index("Status") + 1 if "Status" in cols_planilha else 3
        COL_RESP = cols_planilha.index("Responsavel") + 1 if "Responsavel" in cols_planilha else 5
        COL_INICIO = cols_planilha.index("Inicio") + 1 if "Inicio" in cols_planilha else 6
        COL_FIM = cols_planilha.index("Data_Conclusao") + 1 if "Data_Conclusao" in cols_planilha else 7
    else:
        COL_STATUS, COL_RESP, COL_INICIO, COL_FIM = 3, 5, 6, 7
    
    # ===================================================
    # 📺 MODO TELÃO (TV DO SALÃO - SLA COMPLETO)
    # ===================================================
    if usuario == "TV":
        st.markdown("<h1 style='text-align: center; color: #1E90FF; font-size: 70px;'>📺 Dashboard Operacional</h1>", unsafe_allow_html=True)
        st.markdown(f"<p style='text-align: center; font-size: 25px;'>Última atualização: {hora_texto()}</p>", unsafe_allow_html=True)
        
        st.markdown("""
            <style>
            [data-testid="stSidebar"] {display: none;} 
            header {display: none;} 
            .block-container {padding-top: 1rem; max-width: 95%;}
            [data-testid="stMetricValue"] {font-size: 60px !important; line-height: 1.2;}
            [data-testid="stMetricLabel"] {font-size: 22px !important; font-weight: bold;}
            </style>
        """, unsafe_allow_html=True)

        if st.button("Sair (Logout da TV)"):
            del st.session_state['usuario']; st.rerun()
            
        # --- CÁLCULOS ---
        qtd_online = len(df_equipe[df_equipe['Status'] == 'Disponivel']) if not df_equipe.empty and 'Status' in df_equipe.columns else 0
        
        # 0. Base Geral
        total_base = len(df) if not df.empty else 0
        base_fora = 0
        base_dentro = 0
        if not df.empty and 'SLA' in df.columns:
            base_fora = len(df[df['SLA'].astype(str).str.lower().str.contains('fora')])
            base_dentro = total_base - base_fora
        else: base_dentro = total_base

        # 1. Pendentes
        pend_total = 0; pend_dentro = 0; pend_fora = 0
        if not df.empty:
            pend_df = df[df['Status'] == 'Pendente'].copy()
            pend_total = len(pend_df)
            if pend_total > 0 and 'SLA' in pend_df.columns:
                pend_fora = len(pend_df[pend_df['SLA'].astype(str).str.lower().str.contains('fora')])
                pend_dentro = pend_total - pend_fora
            else: pend_dentro = pend_total
                
        # 2. Em Andamento
        and_total = 0; and_dentro = 0; and_fora = 0
        if not df.empty:
            and_df = df[df['Status'] == 'Em Andamento'].copy()
            and_total = len(and_df)
            if and_total > 0 and 'SLA' in and_df.columns:
                and_fora = len(and_df[and_df['SLA'].astype(str).str.lower().str.contains('fora')])
                and_dentro = and_total - and_fora
            else: and_dentro = and_total
                
        # 3. Feitos Hoje
        feitos_total = 0; feitos_dentro = 0; feitos_fora = 0
        if not df.empty and 'Data_Conclusao' in df.columns:
            hoje = data_hoje()
            feitos_df = df[(df['Status'] == 'Concluido') & (df['Data_Conclusao'].astype(str).str.contains(hoje))].copy()
            feitos_total = len(feitos_df)
            if feitos_total > 0 and 'SLA' in feitos_df.columns:
                feitos_fora = len(feitos_df[feitos_df['SLA'].astype(str).str.lower().str.contains('fora')])
                feitos_dentro = feitos_total - feitos_fora
            else: feitos_dentro = feitos_total

        # --- EXIBIÇÃO NO TELÃO ---
        st.markdown(f"<h2 style='text-align: center;'>🟢 Operadores Online: {qtd_online}</h2>", unsafe_allow_html=True)
        st.write("---")
        
        st.markdown("### 🗄️ Base Geral (Todos os Status)")
        cb1, cb2, cb3 = st.columns(3)
        cb1.metric("Total na Base", total_base)
        cb2.metric("✅ SLA no Prazo", base_dentro)
        cb3.metric("🔥 SLA Atrasado", base_fora)

        st.write("---")
        st.markdown("### 🎫 Fila de Espera (Pendentes)")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Pendente", pend_total)
        c2.metric("✅ SLA no Prazo", pend_dentro)
        c3.metric("🔥 SLA Fora", pend_fora)
        
        st.write("---")
        st.markdown("### ⚙️ Em Atendimento (Agora)")
        c4, c5, c6 = st.columns(3)
        c4.metric("Total em Andamento", and_total)
        c5.metric("✅ SLA no Prazo", and_dentro)
        c6.metric("🔥 SLA Atrasado", and_fora)
        
        st.write("---")
        st.markdown("### 🏆 Fechamentos (Hoje)")
        c7, c8, c9 = st.columns(3)
        c7.metric("Total Concluído", feitos_total)
        c8.metric("🟢 SLA no Prazo", feitos_dentro)
        c9.metric("🔴 SLA Atrasadas", feitos_fora)
        
        time.sleep(15)
        st.cache_data.clear(); st.rerun()

    # ===================================================
    # 👨‍💻 VISÃO NORMAL (GERENTE OU OPERADOR)
    # ===================================================
    else:
        status_real = "Erro"
        linha_planilha = None
        minhas_etapas = ['Todas']
        
        if not df_equipe.empty and 'Colaboradores' in df_equipe.columns:
            meus_dados = df_equipe[df_equipe['Colaboradores'] == usuario]
            if not meus_dados.empty:
                status_real = meus_dados.iloc[0].get('Status', 'Erro')
                etapas_str = str(meus_dados.iloc[0].get('Etapas_Permitidas', 'Todas'))
                minhas_etapas = [e.strip() for e in etapas_str.split(',')]
                idx = df_equipe.index[df_equipe['Colaboradores'] == usuario].tolist()[0] + 2
                linha_planilha = idx

        with st.sidebar:
            st.header(f"👤 {usuario}")
            
            novo_tema = st.selectbox("🎨 Tema Visual", ["Padrão", "Hacker
