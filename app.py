import streamlit as st
import pandas as pd
import gspread
from datetime import datetime, timedelta
import time
import pytz
import os
import requests
import streamlit.components.v1 as components
import google.generativeai as genai
from streamlit_autorefresh import st_autorefresh
from fpdf import FPDF
import unicodedata
import base64

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(page_title="Sistema de Chamados", page_icon="🎫", layout="wide")

# --- 👑 ADMINISTRAÇÃO ---
ADMINS = ["Eduardo", "EduardoSouza", "Gestor"] 

# --- 🧠 CONFIGURAÇÃO DA IA (ORÁCULO E RESUMIDOR) ---
try:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    
    modelo_escolhido = None
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            if 'flash' in m.name: 
                modelo_escolhido = m.name
                break
    
    if not modelo_escolhido:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                modelo_escolhido = m.name
                break

    if modelo_escolhido and modelo_escolhido.startswith("models/"):
        modelo_escolhido = modelo_escolhido.replace("models/", "")
        
    modelo_oraculo = genai.GenerativeModel(modelo_escolhido)
    ia_ativa = True
except Exception as e:
    modelo_oraculo = None
    ia_ativa = False
    erro_ia = str(e)

# --- 🚨 ALERTA MICROSOFT TEAMS ---
def alertar_teams(mensagem):
    # COLOQUE AQUI O WEBHOOK DO SEU CANAL DO TEAMS (A TI PODE FORNECER)
    webhook_url = "https://teams.microsoft.com/l/chat/48:notes/conversations?context=%7B%22contextType%22%3A%22chat%22%7D"
    if webhook_url != "https://teams.microsoft.com/l/chat/48:notes/conversations?context=%7B%22contextType%22%3A%22chat%22%7D":
        try:
            payload = {"text": mensagem}
            requests.post(webhook_url, json=payload)
        except:
            pass

# --- CONEXÃO ULTRA-OTIMIZADA ---
@st.cache_resource
def conectar_e_abrir_abas():
    try:
        if os.path.exists("credentials.json"):
            client = gspread.service_account(filename="credentials.json")
        else:
            try:
                creds_dict = st.secrets["gcp_service_account"]
                client = gspread.service_account_from_dict(creds_dict)
            except:
                return "🚨 ERRO: Credenciais não encontradas.", None, None, None, None
        
        sh = client.open("Sistema_Chamados_TESTE")
        abas = sh.worksheets()
        
        aba_chamados = abas[0] if len(abas) > 0 else None
        aba_users = abas[1] if len(abas) > 1 else None
        aba_logs = abas[2] if len(abas) > 2 else None
        aba_transp = abas[3] if len(abas) > 3 else None
        
        return sh, aba_chamados, aba_users, aba_logs, aba_transp
    except Exception as e:
        return f"Erro do Google: {str(e)}", None, None, None, None

# --- FUNÇÕES DE TEMPO E LÓGICA ---
def hora_brasil():
    fuso = pytz.timezone('America/Sao_Paulo')
    return datetime.now(fuso) 

def hora_texto():
    return hora_brasil().strftime("%d/%m/%Y %H:%M:%S")

def data_hoje():
    return hora_brasil().strftime("%d/%m/%Y")

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

# --- INICIAR CONEXÃO ---
sh, aba_chamados, aba_users, aba_logs, aba_transp = conectar_e_abrir_abas()

if isinstance(sh, str):
    st.error(f"Erro de conexão: {sh}")
    st.warning("⏳ Se for erro 429, aguarde 1 minuto e recarregue a página.")
    st.stop()
if aba_chamados is None:
    st.error("Erro ao carregar abas principais.")
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
    except: return pd.DataFrame()

@st.cache_data(ttl=3600) 
def carregar_agenda_transp():
    if aba_transp is None: return pd.DataFrame()
    try: return pd.DataFrame(aba_transp.get_all_records())
    except: return pd.DataFrame()

@st.cache_data(ttl=15)
def carregar_logs_dia():
    if aba_logs is None: return pd.DataFrame()
    try:
        dados = aba_logs.get_all_values()
        if not dados: return pd.DataFrame()
        
        if dados[0][0].lower() in ['usuario', 'nome', 'operador']:
            df_l = pd.DataFrame(dados[1:], columns=["Usuario", "Acao", "DataHora"])
        else:
            df_l = pd.DataFrame(dados, columns=["Usuario", "Acao", "DataHora"])
        return df_l
    except: return pd.DataFrame()

def ler_mural():
    try:
        with open("mural.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except:
        return ""

def salvar_mural(texto):
    with open("mural.txt", "w", encoding="utf-8") as f:
        f.write(texto)

# --- RANKING GLOBAL (MEMÓRIA PERPÉTUA) ---
df_logs_global = carregar_logs_dia()
ranking_global = pd.DataFrame()
if not df_logs_global.empty:
    hoje = data_hoje()
    logs_hoje = df_logs_global[df_logs_global['DataHora'].astype(str).str.contains(hoje)]
    feitos_logs = logs_hoje[logs_hoje['Acao'].astype(str).str.contains("Finalizou", case=False)]
    if not feitos_logs.empty:
        ranking_global = feitos_logs['Usuario'].value_counts().reset_index()
        ranking_global.columns = ['Nome', 'Qtd']

# EFEITO BALÕES
if 'soltar_baloes' in st.session_state and st.session_state['soltar_baloes']:
    st.balloons()
    st.session_state['soltar_baloes'] = False

# ===================================================
# 🎨 APLICADOR DE TEMAS
# ===================================================
if 'tema_escolhido' not in st.session_state:
    st.session_state['tema_escolhido'] = "Padrão"

if st.session_state['tema_escolhido'] == "Hacker Matrix":
    st.markdown("""
        <style>
        .stApp { background-color: #0D0D0D; }
        h1, h2, h3, h4, p, label, li { color: #00FF41 !important; font-family: 'Courier New', Courier, monospace !important; }
        [data-testid="stMetricValue"], [data-testid="stMetricLabel"] { color: #00FF41 !important; font-family: 'Courier New', Courier, monospace !important; }
        .stButton>button { background-color: #000000; color: #00FF41 !important; border: 1px solid #00FF41; box-shadow: 0 0 5px #00FF41; font-family: 'Courier New', Courier, monospace !important;}
        .stButton>button:hover { background-color: #00FF41; color: #000000 !important; }
        [data-testid="stSidebar"] { background-color: #050505; border-right: 2px solid #00FF41; }
        .stSelectbox>div>div { background-color: #000; color: #00FF41; border: 1px solid #00FF41; }
        .stTextInput>div>div>input { background-color: #000; color: #00FF41; border: 1px solid #00FF41; }
        </style>
    """, unsafe_allow_html=True)
elif st.session_state['tema_escolhido'] == "Dark Night":
    st.markdown("""
        <style>
        .stApp { background-color: #0b1120; }
        h1, h2, h3, h4, p, label, li { color: #e2e8f0 !important; }
        [data-testid="stMetricValue"], [data-testid="stMetricLabel"] { color: #e2e8f0 !important; }
        .stButton>button { background-color: #1e293b; color: #38bdf8 !important; border: 1px solid #38bdf8; }
        .stButton>button:hover { background-color: #38bdf8; color: #0b1120 !important; }
        [data-testid="stSidebar"] { background-color: #0f172a; border-right: 1px solid #1e293b; }
        </style>
    """, unsafe_allow_html=True)
elif st.session_state['tema_escolhido'] == "Rosa Fofo":
    st.markdown("""
        <style>
        .stApp { background-color: #fff0f5; }
        h1, h2, h3, h4, p, label, li { color: #d81b60 !important; font-family: 'Trebuchet MS', sans-serif !important; }
        [data-testid="stMetricValue"], [data-testid="stMetricLabel"] { color: #d81b60 !important; font-family: 'Trebuchet MS', sans-serif !important; }
        .stButton>button { background-color: #ffb6c1; color: #fff !important; border: 2px solid #ff69b4; border-radius: 20px; font-family: 'Trebuchet MS', sans-serif !important;}
        .stButton>button:hover { background-color: #ff69b4; color: #fff !important; }
        [data-testid="stSidebar"] { background-color: #ffe4e1; border-right: 2px solid #ffb6c1; }
        .stSelectbox>div>div { background-color: #fff; color: #d81b60; border: 2px solid #ffb6c1; border-radius: 15px;}
        .stTextInput>div>div>input { background-color: #fff; color: #d81b60; border: 2px solid #ffb6c1; border-radius: 15px;}
        </style>
    """, unsafe_allow_html=True)

# ===================================================
# 🎫 TELA DE LOGIN CORPORATIVA BLINDADA
# ===================================================
if 'usuario' not in st.session_state:
    
    df_equipe = carregar_status_equipe()
    if not df_equipe.empty and 'Colaboradores' in df_equipe.columns:
        lista_nomes = [n for n in df_equipe['Colaboradores'].tolist() if str(n).strip() != '']
        senhas = dict(zip(df_equipe['Colaboradores'], df_equipe.get('Senha', ['']*len(df_equipe))))
    else:
        lista_nomes = []; senhas = {}
        st.warning("⚠️ Planilha a carregar. Clique abaixo se demorar.")
        if st.button("🔄 Recarregar Nomes"):
            st.cache_data.clear(); st.rerun()

    # --- CSS BLINDADO PARA O STREAMLIT ---
    st.markdown("""
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        /* Fundo Azul Corporativo */
        .stApp { background: linear-gradient(135deg, #0f1c3a 0%, #173775 100%); }
        [data-testid="stSidebar"], [data-testid="stHeader"] {display: none;}
        
        /* Transforma a coluna central no Cartão Branco */
        div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-of-type(2) {
            background-color: white;
            padding: 40px 30px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
            margin-top: 10vh;
        }

        /* Estilo das caixas de texto */
        div[data-testid="stTextInput"] div[data-baseweb="input"] {
            border: 2px solid lightgray !important;
            border-radius: 8px !important;
            background-color: white !important;
            transition: all 0.3s ease;
        }
        
        /* Escudo Anti-Texto (espaço para o ícone não encostar nas letras) */
        div[data-testid="stTextInput"] div[data-baseweb="input"] input {
            padding-left: 45px !important; 
            height: 50px !important;
            color: #0b1120 !important;
            font-size: 1.1em;
        }
        
        /* Ícone Usuário (1º Campo) - Gravado no lugar exato */
        div[data-testid="stTextInput"]:nth-of-type(1) div[data-baseweb="input"]::before {
            content: "\\f007";
            font-family: "Font Awesome 6 Free"; font-weight: 900;
            position: absolute; left: 15px; top: 15px;
            color: gray; font-size: 1.2em; z-index: 10;
        }
        
        /* Ícone Senha (2º Campo) - Gravado no lugar exato */
        div[data-testid="stTextInput"]:nth-of-type(2) div[data-baseweb="input"]::before {
            content: "\\f023";
            font-family: "Font Awesome 6 Free"; font-weight: 900;
            position: absolute; left: 15px; top: 15px;
            color: gray; font-size: 1.2em; z-index: 10;
        }
        
        /* Realce Azul ao Clicar na Caixa de Texto */
        div[data-testid="stTextInput"] div[data-baseweb="input"]:focus-within {
            border-color: #38bdf8 !important;
            box-shadow: 0 0 10px rgba(56,189,248,0.4) !important;
        }
        
        /* O ícone também fica azul ao focar a caixa! */
        div[data-testid="stTextInput"]:has(input:focus) div[data-baseweb="input"]::before {
            color: #38bdf8 !important;
        }
        
        /* Botão Entrar */
        div[data-testid="stButton"] button {
            background-color: #173775;
            color: white;
            height: 50px;
            border-radius: 25px;
            font-size: 1.2em;
            font-weight: bold;
            border: none;
            width: 100%;
            margin-top: 15px;
            transition: all 0.3s ease;
        }
        div[data-testid="stButton"] button:hover {
            background-color: #38bdf8;
            color: white;
            box-shadow: 0 5px 15px rgba(56,189,248,0.4);
        }
    </style>
    """, unsafe_allow_html=True)

    def get_image_base64(caminho_imagem):
        try:
            with open(caminho_imagem, "rb") as img_file:
                return base64.b64encode(img_file.read()).decode()
        except:
            return "" 
            
    logo_b64 = get_image_base64("logo_frigelar.png")
    
    # Renderiza tudo de forma 100% nativa no Streamlit
    c1, c2, c3 = st.columns([1, 1.2, 1]) 
    
    with c2:
        # A Logo e Textos
        if logo_b64:
            st.markdown(f'<div style="text-align: center;"><img src="data:image/png;base64,{logo_b64}" width="180" style="margin-bottom: 20px;"></div>', unsafe_allow_html=True)
        else:
            st.markdown('<h1 style="color:#173775; font-size: 3em; text-align: center; margin-bottom:10px;">❄️</h1>', unsafe_allow_html=True)
            
        st.markdown('<h1 style="color: #173775; font-size: 2.2em; text-align: center; margin-top: 0; margin-bottom: 5px; font-weight: 700;">BEM-VINDO</h1>', unsafe_allow_html=True)
        st.markdown('<h2 style="color: gray; font-size: 1em; text-align: center; font-weight: 400; margin-top: 0; margin-bottom: 25px;">Sistema de Chamados</h2>', unsafe_allow_html=True)
        
        # Os Campos de Entrada Nativos
        user_digitado = st.text_input("Utilizador", placeholder="Coloque o seu usuário", label_visibility="collapsed")
        senha_digitada = st.text_input("Senha", type="password", placeholder="Coloque a sua senha", label_visibility="collapsed")
        
        # O Botão
        if st.button("ENTRAR", use_container_width=True):
            if user_digitado in lista_nomes and str(senha_digitada) == str(senhas.get(user_digitado, "")):
                st.session_state['usuario'] = user_digitado
                st.session_state['tamanho_fila_anterior'] = 0 
                registrar_log(user_digitado, "LOGIN") 
                
                try:
                    idx = df_equipe.index[df_equipe['Colaboradores'] == user_digitado].tolist()[0] + 2
                    aba_users.update_cell(idx, 3, "Disponivel")
                    st.cache_data.clear() 
                except: pass
                
                st.rerun()
            else: 
                st.error("❌ Login não encontrado ou senha incorreta.")

# ===================================================
# SISTEMA LOGADO
# ===================================================
else:
    usuario = st.session_state['usuario']
    df = carregar_dados_chamados()
    df_equipe = carregar_status_equipe()
    
    if not df.empty:
        cols_planilha = df.columns.tolist()
        COL_STATUS = cols_planilha.index("Status") + 1 if "Status" in cols_planilha else 3
        COL_RESP = cols_planilha.index("Responsavel") + 1 if "Responsavel" in cols_planilha else 5
        COL_INICIO = cols_planilha.index("Inicio") + 1 if "Inicio" in cols_planilha else 6
        COL_FIM = cols_planilha.index("Data_Conclusao") + 1 if "Data_Conclusao" in cols_planilha else 7
    else:
        COL_STATUS, COL_RESP, COL_INICIO, COL_FIM = 3, 5, 6, 7
    
    # ===================================================
    # 📺 MODO TELÃO (TV DO SALÃO)
    # ===================================================
    if usuario == "TV":
        st.markdown("<h1 style='text-align: center; color: #1E90FF; font-size: 70px;'>📺 Dashboard Operacional</h1>", unsafe_allow_html=True)
        
        texto_mural = ler_mural()
        if texto_mural:
            st.warning(f"📢 **AVISO DA GESTÃO:** {texto_mural}")
            
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
            
        qtd_online = len(df_equipe[df_equipe['Status'] == 'Disponivel']) if not df_equipe.empty and 'Status' in df_equipe.columns else 0
        total_base = len(df) if not df.empty else 0
        
        base_fora = len(df[df['SLA'].astype(str).str.lower().str.contains('fora')]) if not df.empty and 'SLA' in df.columns else 0
        base_dentro = total_base - base_fora

        pend_df = df[df['Status'] == 'Pendente'].copy() if not df.empty else pd.DataFrame()
        pend_total = len(pend_df)
        pend_fora = len(pend_df[pend_df['SLA'].astype(str).str.lower().str.contains('fora')]) if pend_total > 0 and 'SLA' in pend_df.columns else 0
        pend_dentro = pend_total - pend_fora
                
        and_df = df[df['Status'] == 'Em Andamento'].copy() if not df.empty else pd.DataFrame()
        and_total = len(and_df)
        and_fora = len(and_df[and_df['SLA'].astype(str).str.lower().str.contains('fora')]) if and_total > 0 and 'SLA' in and_df.columns else 0
        and_dentro = and_total - and_fora
                
        feitos_total = ranking_global['Qtd'].sum() if not ranking_global.empty else 0

        st.markdown(f"<h2 style='text-align: center;'>🟢 Operadores Online: {qtd_online}</h2>", unsafe_allow_html=True)
        st.write("---")
        
        st.markdown("### 🗄️ Base Geral (Todos os Status)")
        cb1, cb2, cb3 = st.columns(3)
        cb1.metric("Total na Base", total_base)
        cb2.metric("✅ Geral no Prazo", base_dentro)
        cb3.metric("🔥 Geral Atrasado", base_fora)

        st.write("---")
        st.markdown("### 🎫 Fila de Espera (Pendentes)")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Pendente", pend_total)
        c2.metric("✅ Fila no Prazo", pend_dentro)
        c3.metric("🔥 Fila Atrasada", pend_fora)
        
        st.write("---")
        st.markdown("### ⚙️ Em Atendimento (Agora)")
        c4, c5, c6 = st.columns(3)
        c4.metric("Total em Andamento", and_total)
        c5.metric("✅ Andamento no Prazo", and_dentro)
        c6.metric("🔥 Andamento Atrasado", and_fora)
        
        st.write("---")
        st.markdown("### 🏆 Fechamentos da Equipa (Hoje)")
        c7, c8 = st.columns(2)
        c7.metric("Total Concluído (Geral)", feitos_total)
        with c8:
            if not ranking_global.empty:
                melhor = ranking_global.iloc[0]
                st.metric("🥇 Destaque do Dia", f"{melhor['Nome']} ({melhor['Qtd']})")
            else:
                st.metric("🥇 Destaque do Dia", "A aguardar...")
        
        time.sleep(15)
        st.cache_data.clear(); st.rerun()

    # ===================================================
    # 👨‍💻 VISÃO NORMAL (GESTOR OU OPERADOR)
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
                idx = df_equipe.index[df_equipe['Colaboradores'] == usuario].tolist()[0]
                linha_planilha = idx + 2

        with st.sidebar:
            st.header(f"👤 {usuario}")
            
            novo_tema = st.selectbox("🎨 Tema Visual", ["Padrão", "Hacker Matrix", "Dark Night", "Rosa Fofo"], index=["Padrão", "Hacker Matrix", "Dark Night", "Rosa Fofo"].index(st.session_state['tema_escolhido']))
            if novo_tema != st.session_state['tema_escolhido']:
                st.session_state['tema_escolhido'] = novo_tema
                st.rerun()
            st.divider()

            modo_gerente = False
            if usuario in ADMINS:
                st.success("👑 Modo Gestor Liberado")
                modo_gerente = st.toggle("Painel de Gestão", value=False)
                st.divider()
            
            st.info(f"Status Atual: **{status_real}**")
            
            c1, c2 = st.columns(2)
            if c1.button("🟢 Online"):
                if linha_planilha:
                    aba_users.update_cell(linha_planilha, 3, "Disponivel")
                    registrar_log(usuario, "Ficou Disponivel")
                    st.cache_data.clear(); st.rerun()
            if c2.button("☕ Pausa"):
                if linha_planilha:
                    aba_users.update_cell(linha_planilha, 3, "Pausa")
                    registrar_log(usuario, "Entrou em Pausa")
                    st.cache_data.clear(); st.rerun()
            if st.button("🚽 Casa de Banho"):
                if linha_planilha:
                    aba_users.update_cell(linha_planilha, 3, "Banheiro")
                    registrar_log(usuario, "Foi à Casa de Banho")
                    st.cache_data.clear(); st.rerun()
            
            st.divider()
            
            with st.expander("⚙️ O Meu Perfil (Mudar Palavra-Passe)"):
                nova_senha = st.text_input("Digite a nova palavra-passe:", type="password")
                confirma_senha = st.text_input("Confirme a nova palavra-passe:", type="password")
                
                if st.button("Guardar Nova Palavra-Passe", use_container_width=True):
                    if nova_senha == confirma_senha and len(nova_senha) >= 4:
                        try:
                            cols_users = df_equipe.columns.tolist()
                            if "Senha" in cols_users:
                                col_senha_idx = cols_users.index("Senha") + 1
                                aba_users.update_cell(linha_planilha, col_senha_idx, nova_senha)
                                registrar_log(usuario, "Mudou a própria palavra-passe")
                                st.success("✅ Palavra-passe atualizada! Use-a no próximo login.")
                                st.cache_data.clear()
                            else:
                                st.error("Erro: Coluna 'Senha' não encontrada.")
                        except Exception as e:
                            st.error(f"Erro ao mudar palavra-passe: {e}")
                    else:
                        st.warning("⚠️ As palavras-passe não coincidem ou são curtas (mín. 4).")
            
            st.divider()
            
            st.subheader("🏆 O Seu Desempenho Hoje")
            if not ranking_global.empty:
                minha_posicao = ranking_global.index[ranking_global['Nome'] == usuario].tolist()
                
                if minha_posicao:
                    pos_real = minha_posicao[0] + 1
                    qtd_minha = ranking_global.iloc[minha_posicao[0]]['Qtd']
                    st.markdown(f"✅ **Feitos hoje:** {qtd_minha} chamados")
                    if pos_real == 1: st.success(f"🥇 Está no 1º Lugar da equipa!")
                    elif pos_real == 2: st.info(f"🥈 Está no 2º Lugar da equipa!")
                    elif pos_real == 3: st.warning(f"🥉 Está no 3º Lugar da equipa!")
                    else: st.markdown(f"📍 **A sua posição no ranking:** {pos_real}º Lugar")
                else: st.caption("Ainda não finalizou chamados hoje.")

                if usuario in ADMINS:
                    st.write("---")
                    st.caption("👑 Visão do Gestor (Top 3):")
                    for i, row in ranking_global.head(3).iterrows():
                        medalha = "🥇" if i == 0 else "🥈" if i == 1 else "🥉"
                        st.markdown(f"{medalha} {row['Nome']} ({row['Qtd']})")
            else: st.caption("A corrida de hoje ainda não começou!")

            st.divider()
            if st.button("Sair (Logout)"):
                registrar_log(usuario, "LOGOUT") 
                del st.session_state['usuario']; st.rerun()

        # ===================================================
        # 👑 VISÃO DO GESTOR (ADMIN)
        # ===================================================
        if modo_gerente:
            st.title("📊 Painel de Controlo - Gestão")
            st.caption(f"Última atualização: {hora_texto()}")
            if st.button("🔄 Atualizar Tudo"): st.cache_data.clear(); st.rerun()

            # ===================================================
            # 📄 GERADOR DE RELATÓRIO EXECUTIVO (PDF)
            # ===================================================
            st.write("---")
            st.subheader("📄 Relatório Executivo (PDF)")
            with st.expander("Gerar Fechamento do Turno em PDF"):
                st.write("Este robô cruza os dados do Log (Produção) com a Fila Atual para montar o documento oficial da Diretoria.")
                
                aviso_pdf = st.text_input("Observação da Gestão (Opcional):", placeholder="Ex: Foco nos atrasos da Transportadora X")
                
                if st.button("🖨️ Mapear Dados e Criar PDF", use_container_width=True):
                    with st.spinner("Compilando dados em tempo real..."):
                        
                        def formatar_texto(texto):
                            return unicodedata.normalize('NFKD', str(texto)).encode('ASCII', 'ignore').decode('ASCII')
                        
                        pendentes_totais = len(df[df['Status'] == 'Pendente']) if not df.empty else 0
                        prioridade_df = df[(df['Status'] == 'Pendente') & (df['SLA'].astype(str).str.contains('Prioridade', case=False))] if not df.empty and 'SLA' in df.columns else pd.DataFrame()
                        prioridade_1_totais = len(prioridade_df)
                        feitos_totais = ranking_global['Qtd'].sum() if not ranking_global.empty else 0
                        aviso = ler_mural()
                        
                        pdf = FPDF()
                        pdf.add_page()
                        
                        pdf.set_font('Arial', 'B', 16)
                        pdf.cell(0, 10, formatar_texto('Relatorio Executivo - SAC Frigelar'), 0, 1, 'C')
                        pdf.set_font('Arial', 'I', 10)
                        pdf.cell(0, 10, formatar_texto(f'Gerado pelo Sistema Automatizado em: {hora_texto()}'), 0, 1, 'C')
                        pdf.ln(10)
                        
                        pdf.set_font('Arial', 'B', 12)
                        pdf.cell(0, 10, formatar_texto('1. PANORAMA OPERACIONAL (Fila vs Producao)'), 0, 1)
                        pdf.set_font('Arial', '', 12)
                        pdf.cell(0, 10, formatar_texto(f'> Chamados Finalizados Hoje: {feitos_totais} chamados resolvidos.'), 0, 1)
                        pdf.cell(0, 10, formatar_texto(f'> Fila de Espera Atual: {pendentes_totais} pendentes na esteira.'), 0, 1)
                        
                        if prioridade_1_totais > 0:
                            pdf.set_text_color(255, 0, 0)
                            pdf.cell(0, 10, formatar_texto(f'> ALERTA DE SLA: {prioridade_1_totais} chamados de Prioridade 1 (Vencem Hoje) na fila!'), 0, 1)
                            pdf.set_text_color(0, 0, 0)
                        else:
                            pdf.set_text_color(0, 128, 0)
                            pdf.cell(0, 10, formatar_texto('> ALERTA DE SLA: Nenhum chamado de Prioridade Maxima pendente. Operacao controlada.'), 0, 1)
                            pdf.set_text_color(0, 0, 0)
                        pdf.ln(5)
                        
                        pdf.set_font('Arial', 'B', 12)
                        pdf.cell(0, 10, formatar_texto('2. DESTAQUES DA EQUIPE (Top 3 Produtividade)'), 0, 1)
                        pdf.set_font('Arial', '', 12)
                        if not ranking_global.empty:
                            for i, row in ranking_global.head(3).iterrows():
                                pdf.cell(0, 10, formatar_texto(f"{i+1} Lugar: {row['Nome']} - {row['Qtd']} concluídos"), 0, 1)
                        else:
                            pdf.cell(0, 10, formatar_texto('A equipe ainda não finalizou chamados nesta rodada.'), 0, 1)
                        pdf.ln(5)
                        
                        if aviso_pdf:
                            pdf.set_font('Arial', 'B', 12)
                            pdf.cell(0, 10, formatar_texto('3. DIRETRIZ DA GESTAO'), 0, 1)
                            pdf.set_font('Arial', 'I', 11)
                            pdf.multi_cell(0, 10, formatar_texto(aviso_pdf))
                            
                        nome_arquivo = f"Fechamento_SAC_{datetime.now().strftime('%d%m%Y_%H%M')}.pdf"
                        pdf.output(nome_arquivo, 'F')
                        
                        with open(nome_arquivo, "rb") as f:
                            bytes_pdf = f.read()
                            
                        st.success("✅ PDF Gerado com Sucesso!")
                        st.download_button(
                            label="📥 BAIXAR RELATÓRIO PDF AGORA",
                            data=bytes_pdf,
                            file_name=nome_arquivo,
                            mime="application/pdf",
                            type="primary",
                            use_container_width=True
                        )
            
            st.write("---")
            st.subheader("📢 Mural de Avisos (Recado para a Equipa)")
            aviso_atual = ler_mural()
            novo_aviso = st.text_area("Digite o recado (deixe em branco para apagar o aviso atual):", value=aviso_atual)
            if st.button("Guardar Aviso no Telão", use_container_width=True):
                salvar_mural(novo_aviso)
                st.success("✅ Mural atualizado! Todos os operadores verão este aviso no ecrã agora.")
                time.sleep(1)
                st.rerun()
            
            st.write("---")
            st.subheader("📥 Robô Importador (Qualitor -> Esteira)")
            with st.expander("Subir nova base de chamados (Substituição Total)"):
                arquivo_excel = st.file_uploader("Arraste o ficheiro bruto do Qualitor aqui (.xlsx ou .csv)", type=["xlsx", "xls", "csv"])
                
                if arquivo_excel is not None:
                    try:
                        with st.spinner("O Robô está a ler o ficheiro..."):
                            if arquivo_excel.name.endswith('.csv'):
                                df_bruto = pd.read_csv(arquivo_excel, sep=None, engine='python', encoding='latin-1')
                            else:
                                df_bruto = pd.read_excel(arquivo_excel)
                        
                        if 'PROCESSO' in df_bruto.columns and 'Chamado' in df_bruto.columns:
                            
                            df_filtrado = df_bruto[~df_bruto['PROCESSO'].astype(str).str.contains("SOLICITANTE ATUALIZAR INFORMAÇÕES", na=False)].copy()
                            
                            de_para = {
                                "(SAC) - ARREPENDIMENTO V3": "Arrependimento",
                                "(SAC) - CANCELAMENTO V3": "Cancelamento de pedido",
                                "(SAC) - ATRASO V3": "Atraso de Entrega",
                                "(SAC) - PRODUTO ERRADO V3": "Produto Errado",
                                "(SAC) - AVARIA V3": "Avaria",
                                "(SAC) - ESTORNADOS": "Estornados",
                                "(SAC) - EXTRAVIO V6": "Extravio"
                            }
                            df_filtrado['Etapa_Limpa'] = df_filtrado['PROCESSO'].map(de_para).fillna(df_filtrado['PROCESSO'])
                            
                            if 'Etapa' in df_filtrado.columns:
                                filtro_mktp = df_filtrado['Etapa'].astype(str).str.contains("REEMBOLSO MKTP", na=False, case=False)
                                df_filtrado.loc[filtro_mktp, 'Etapa_Limpa'] = "Reembolso MKTP"
                            
                            def definir_prioridade(linha):
                                if "PRIORIDADE 1" in str(linha).upper():
                                    return "🔥 Prioridade (Vence Hoje)"
                                return "Normal ✅"
                            
                            df_filtrado['SLA_Final'] = df_filtrado['Lista'].apply(definir_prioridade) if 'Lista' in df_filtrado.columns else "Normal ✅"

                            df_novo = pd.DataFrame()
                            df_novo['ID'] = "" 
                            df_novo['Dados'] = df_filtrado['Chamado'].astype(str).str.replace(r'\.0$', '', regex=True) 
                            df_novo['Status'] = "Pendente" 
                            df_novo['Etapa'] = df_filtrado['Etapa_Limpa'] 
                            df_novo['SLA'] = df_filtrado['SLA_Final'] 
                            df_novo['Responsavel'] = "" 
                            df_novo['Inicio'] = "" 
                            df_novo['Data_Conclusao'] = "" 
                            
                            df_pronto_para_subir = df_novo.drop_duplicates(subset=['Dados'])
                            qtd_novos = len(df_pronto_para_subir)
                            qtd_prioridade = len(df_pronto_para_subir[df_pronto_para_subir['SLA'].str.contains("Prioridade")])
                            
                            st.success(f"✅ Análise concluída! Este ficheiro substituirá a base atual.")
                            st.metric("Total de Chamados para a Fila", qtd_novos)
                            if qtd_prioridade > 0:
                                st.warning(f"⚠️ Atenção: Há {qtd_prioridade} chamados de Prioridade Máxima neste lote!")
                            
                            if qtd_novos > 0:
                                st.dataframe(df_pronto_para_subir[['Dados', 'Etapa', 'SLA']].head(10), hide_index=True)
                                
                                if st.button("🚀 SUBSTITUIR BASE E INJETAR FILA", type="primary", use_container_width=True):
                                    with st.spinner("A apagar base antiga, alinhar colunas e subir nova..."):
                                        df_limpo = df_pronto_para_subir.fillna("")
                                        df_limpo = df_limpo.replace(['nan', 'NaN', 'NaT', 'None'], "")
                                        
                                        cabecalhos = ["ID", "Dados", "Status", "Etapa", "SLA", "Responsavel", "Inicio", "Data_Conclusao"]
                                        dados_finais = [cabecalhos] + df_limpo.values.tolist()
                                        
                                        aba_chamados.clear()
                                        aba_chamados.append_rows(dados_finais)
                                        
                                        registrar_log(usuario, f"Resetou a base e importou {qtd_novos} chamados")
                                        
                                        if qtd_prioridade > 0:
                                            alertar_teams(f"🚨 ALERTA DA GESTÃO: Nova base importada com {qtd_prioridade} chamados de PRIORIDADE 1 (SLA a vencer hoje). Foco total da equipa de SAC!")
                                        
                                        st.success("Tudo pronto! Base antiga apagada e fila nova atualizada.")
                                        st.cache_data.clear()
                                        time.sleep(2)
                                        st.rerun()
                            else:
                                st.info("Nenhum chamado válido encontrado no ficheiro.")
                        else:
                            st.error("Erro: Colunas 'Chamado' ou 'PROCESSO' não encontradas.")
                    except Exception as e:
                        st.error(f"Erro fatal no robô: {e}")

            st.write("---")
            st.subheader("🚨 Monitorização de SLA (Em Andamento)")
            em_andamento = pd.DataFrame()
            if not df.empty:
                em_andamento = df[df['Status'] == 'Em Andamento'].copy()
                if not em_andamento.empty:
                    lista_sla = []
                    for index, row in em_andamento.iterrows():
                        status_visual = row.get('SLA', 'Sem Info')
                        if 'fora' in str(status_visual).lower():
                            status_visual = f"🔥 {status_visual}"
                        else:
                            status_visual = f"✅ {status_visual}"
                            
                        lista_sla.append({
                            "Chamado": row.get('Dados', ''),
                            "Etapa": row.get('Etapa', ''),
                            "Responsável": row.get('Responsavel', ''),
                            "SLA": status_visual,
                            "ID": row.get('ID')
                        })
                    df_sla = pd.DataFrame(lista_sla)
                    st.dataframe(df_sla, hide_index=True, use_container_width=True)
                else: st.success("Equipa livre! Nenhum chamado em andamento.")

            st.write("---")
            st.subheader("🛠️ Ações de Emergência")
            if not em_andamento.empty:
                opcoes = em_andamento.apply(lambda x: f"L{x.name + 2} - ID {x['ID']} - {x['Dados']} ({x['Responsavel']})", axis=1).tolist()
                selecionado = st.selectbox("Selecione um chamado travado:", [""] + opcoes)
                
                if selecionado:
                    linha_trava = int(selecionado.split(" - ")[0].replace("L", ""))
                    col_dev, col_forcar = st.columns(2)

                    if col_dev.button("↩️ Devolver à Fila"):
                        try:
                            aba_chamados.update_cell(linha_trava, COL_STATUS, "Pendente") 
                            aba_chamados.update_cell(linha_trava, COL_RESP, "") 
                            aba_chamados.update_cell(linha_trava, COL_INICIO, "") 
                            registrar_log(usuario, f"ADMIN: Devolveu linha {linha_trava}")
                            st.success("Devolvido!")
                            st.cache_data.clear(); time.sleep(1); st.rerun()
                        except Exception as e: st.error(f"Erro: {e}")

                    if col_forcar.button("🏁 Forçar Conclusão"):
                        try:
                            aba_chamados.update_cell(linha_trava, COL_STATUS, "Concluido") 
                            aba_chamados.update_cell(linha_trava, COL_FIM, hora_texto()) 
                            registrar_log(usuario, f"ADMIN: Forçou conclusão linha {linha_trava}")
                            st.success("Encerrado!")
                            st.cache_data.clear(); time.sleep(1); st.rerun()
                        except Exception as e: st.error(f"Erro: {e}")
            else: st.info("Nada para destravar.")

            st.write("---")
            c_status, c_prod = st.columns(2)
            with c_status:
                st.subheader("🚦 Equipa Online")
                if not df_equipe.empty:
                    cols = [c for c in df_equipe.columns if c in ['Colaboradores','Status']]
                    st.dataframe(df_equipe[cols], hide_index=True, use_container_width=True)
            with c_prod:
                st.subheader("🏆 Produção Hoje (Dados do Log)")
                if not ranking_global.empty:
                    st.dataframe(ranking_global, hide_index=True, use_container_width=True)
                else: st.info("Sem dados hoje.")

        # ===================================================
        # 👷 VISÃO DO OPERADOR
        # ===================================================
        else:
            texto_mural = ler_mural()
            if texto_mural:
                st.warning(f"📢 **AVISO DA GESTÃO:** {texto_mural}")
                
            if status_real != "Disponivel":
                st.warning(f"⚠️ **ESTÁ EM PAUSA ({status_real})**")
            else:
                st.success("🟢 ONLINE - A aguardar chamados...")
                
                if df.empty:
                    st.write("Sem dados.")
                    if st.button("Recarregar"): st.cache_data.clear(); st.rerun()
                else:
                    meu_chamado = df[(df['Status'] == 'Em Andamento') & (df['Responsavel'] == usuario)]
                    
                    if len(meu_chamado) > 0:
                        if len(meu_chamado) > 1:
                            st.warning("⚠️ Atenção: Tem mais de um chamado em andamento. Finalize este primeiro.")

                        dados = meu_chamado.iloc[0]
                        num = dados.get('Dados', 'N/A') 
                        etapa_atual = dados.get('Etapa', 'N/A')
                        sla_atual = str(dados.get('SLA', 'Sem Info'))
                        
                        if 'fora' in sla_atual.lower():
                            st.error(f"🔥 ALERTA DE SLA: {sla_atual}")
                        else:
                            st.info(f"✅ Status do SLA: {sla_atual}")

                        st.markdown(f"### 📞 Chamado: **{num}** | Etapa: **{etapa_atual}**")
                        if str(num) != 'N/A':
                            link = f"https://frigelar.qualitorsoftware.com/html/hd/hdchamado/cadastro_chamado.php?cdchamado={num}"
                            st.link_button("🔗 Abrir no Qualitor", link)
                            
                        # 🧠 NOVO: RESUMIDOR DE IA
                        with st.expander("✨ Resumir Histórico do Chamado (Inteligência Artificial)"):
                            historico = st.text_area("Cole aqui os assentamentos do cliente para análise rápida:", height=100)
                            if st.button("Mastigar Histórico"):
                                if ia_ativa and historico:
                                    with st.spinner("A processar os dados..."):
                                        prompt = f"Analise este histórico de atendimento e devolva os 3 pontos mais importantes (causa, situação atual e o que o cliente quer). Seja extremamente resumido:\n\n{historico}"
                                        try:
                                            resp = modelo_oraculo.generate_content(prompt)
                                            st.info(resp.text)
                                        except Exception as e:
                                            st.error(f"Erro na IA: {e}")
                                elif not ia_ativa:
                                    st.error("IA desligada. Verifique a chave de API.")
                                else:
                                    st.warning("Cole o texto primeiro.")
                        
                        st.write("---")
                        if 'confirmar' not in st.session_state: st.session_state['confirmar'] = False
                        
                        if not st.session_state['confirmar']:
                            if st.button("✅ FINALIZAR", type="primary"):
                                st.session_state['confirmar'] = True; st.rerun()
                        else:
                            st.warning("Confirma a conclusão?")
                            cy, cn = st.columns(2)
                            if cy.button("👍 SIM"):
                                try:
                                    idx_linha = int(meu_chamado.index[0]) + 2 
                                    aba_chamados.update_cell(idx_linha, COL_STATUS, "Concluido") 
                                    aba_chamados.update_cell(idx_linha, COL_FIM, hora_texto()) 
                                    
                                    registrar_log(usuario, f"Finalizou {num}")
                                    st.session_state['confirmar'] = False
                                    
                                    feitos = 0
                                    if not ranking_global.empty:
                                        minha_linha = ranking_global[ranking_global['Nome'] == usuario]
                                        if not minha_linha.empty:
                                            feitos = minha_linha.iloc[0]['Qtd']
                                        
                                    if (feitos + 1) in [10, 25, 50, 100]:
                                        st.session_state['soltar_baloes'] = True
                                        
                                    st.cache_data.clear(); time.sleep(1); st.rerun()
                                except Exception as e: st.error(f"Erro ao guardar: {e}")
                            
                            if cn.button("❌ NÃO"):
                                st.session_state['confirmar'] = False; st.rerun()
                    
                    else:
                        fila = df[(df['Status'] == 'Pendente') & (df['Responsavel'] == "")].copy()
                        if "Todas" not in minhas_etapas and "todas" not in [e.lower() for e in minhas_etapas]:
                            fila = fila[fila['Etapa'].astype(str).isin(minhas_etapas)]

                        qtd = len(fila)
                        
                        if 'tamanho_fila_anterior' not in st.session_state:
                            st.session_state['tamanho_fila_anterior'] = qtd
                            
                        if qtd > st.session_state['tamanho_fila_anterior']:
                            st.toast("🔔 Novo chamado entrou na fila!")
                            som_url = f"https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3?t={time.time()}"
                            st.markdown(f'<audio autoplay="true"><source src="{som_url}" type="audio/mpeg"></audio>', unsafe_allow_html=True)
                        
                        st.session_state['tamanho_fila_anterior'] = qtd 
                        
                        c_f, c_r = st.columns([3,1])
                        c_f.metric("A Sua Fila de Espera (Permitida)", qtd)
                        if c_r.button("🔄 Atualizar Fila"): st.cache_data.clear(); st.rerun()
                        
                        if qtd > 0:
                            if st.button("📥 PEGAR PRÓXIMO", type="primary", use_container_width=True):
                                try:
                                    if not fila.empty:
                                        if 'SLA' in fila.columns:
                                            fila['Peso_SLA'] = fila['SLA'].astype(str).apply(lambda x: 1 if 'fora' in x.lower() else 2)
                                            fila = fila.sort_values(by='Peso_SLA', kind='stable')
                                        
                                        item = fila.iloc[0]
                                        num_l = str(item.get('Dados','')).replace('.0','')
                                        idx_linha = int(item.name) + 2 
                                        agora = hora_texto()
                                        
                                        aba_chamados.update_cell(idx_linha, COL_STATUS, "Em Andamento") 
                                        aba_chamados.update_cell(idx_linha, COL_RESP, usuario)        
                                        aba_chamados.update_cell(idx_linha, COL_INICIO, agora)          
                                        registrar_log(usuario, f"Pegou {num_l} (Etapa {item.get('Etapa','')})")
                                        
                                        st.cache_data.clear(); time.sleep(1); st.rerun()
                                    else: st.warning("Alguém pegou antes!"); time.sleep(1); st.rerun()
                                except Exception as e: st.error(f"Erro ao pegar chamado: {e}")
                        else: 
                            st.caption("Sem chamados na sua alçada. A fila será verificada automaticamente a cada 60 segundos.")
                            
                            # 🔄 ATUALIZAÇÃO SILENCIOSA (SEM PERDER O LOGIN)
                            st_autorefresh(interval=60000, limit=None, key="refresh_fila_vazia")

            # --- HISTÓRICO VISUAL ---
            st.write("---")
            if not df.empty:
                hist = df[(df['Status']=='Concluido') & (df['Responsavel']==usuario)].copy()
                if not hist.empty and 'Data_Conclusao' in hist.columns:
                    hoje = data_hoje()
                    hist_hoje = hist[hist['Data_Conclusao'].astype(str).str.contains(hoje)].copy()
                    qtd_hoje = len(hist_hoje)
                    st.subheader(f"✅ Os Seus Concluídos nesta rodada: **{qtd_hoje}**")
                    st.caption("O Ranking Oficial no menu lateral não zera se a gestão atualizar a base!")
                    
                    if qtd_hoje > 0:
                        hist_hoje['Link'] = "https://frigelar.qualitorsoftware.com/html/hd/hdchamado/cadastro_chamado.php?cdchamado=" + hist_hoje['Dados'].astype(str)
                        hist_hoje['Tempo_Gasto'] = hist_hoje.apply(lambda row: calcular_duracao_str(row.get('Inicio', ''), row.get('Data_Conclusao', '')), axis=1)
                        
                        hist_hoje = hist_hoje.rename(columns={'Data_Conclusao': 'Horário'})
                        cols_show = ['Link', 'Etapa', 'SLA', 'Tempo_Gasto', 'Horário'] if 'SLA' in hist_hoje.columns else ['Link', 'Etapa', 'Tempo_Gasto', 'Horário']
                        
                        st.dataframe(hist_hoje[cols_show].tail(15), hide_index=True, use_container_width=True,
                            column_config={"Link": st.column_config.LinkColumn("Chamado", display_text=r"cdchamado=(.*)")})
                    else: st.caption("Finalize o primeiro chamado para aparecer aqui!")

        # ===================================================
        # 🧙‍♂️ GAVETA DO ORÁCULO (INTELIGÊNCIA ARTIFICIAL)
        # ===================================================
        st.write("---")
        with st.expander("🧙‍♂️ Oráculo Frigelar - Tire as suas dúvidas da operação"):
            pergunta = st.text_input("O que precisa de saber?", placeholder="Ex: Qual o prazo de devolução?")
            
            if st.button("✨ Perguntar ao Oráculo"):
                if ia_ativa:
                    try:
                        with open("regras_operacao.txt", "r", encoding="utf-8") as f:
                            texto_regras = f.read()
                        
                        with st.spinner("O Oráculo está a consultar o manual..."):
                            comando = f"""
                            Você é o Oráculo, um assistente interno sênior do SAC da Frigelar.
                            Responda a pergunta do operador baseando-se EXCLUSIVAMENTE no manual abaixo.
                            Se a resposta NÃO estiver no manual, diga exatamente: "Desculpe, não encontrei essa informação nas minhas regras atuais. Consulte o Supervisor."
                            Seja direto, claro e educado.

                            MANUAL DA OPERAÇÃO:
                            {texto_regras}

                            PERGUNTA DO OPERADOR: {pergunta}
                            """
                            
                            resposta = modelo_oraculo.generate_content(comando)
                            st.info(resposta.text)
                    
                    except FileNotFoundError:
                        st.error("🚨 Ficheiro 'regras_operacao.txt' não encontrado. Crie o ficheiro na mesma pasta do sistema.")
                    except Exception as e:
                        st.error(f"🚨 Erro ao processar a resposta: {e}")
                else:
                    st.error(f"🚨 IA não configurada corretamente. Verifique a chave no secrets.toml. Erro: {erro_ia}")

        # ===================================================
        # 🚚 GAVETA DE TRANSPORTADORAS
        # ===================================================
        st.write("---")
        with st.expander("🚚 Agenda de Contactos - Transportadoras"):
            df_transp = carregar_agenda_transp()
            if not df_transp.empty and 'Transportadora' in df_transp.columns:
                lista_t = [str(t) for t in df_transp['Transportadora'].dropna().unique() if str(t).strip() != '']
                escolha_t = st.selectbox("Selecione a Transportadora:", [""] + sorted(lista_t))
                if escolha_t:
                    dados_t = df_transp[df_transp['Transportadora'] == escolha_t].iloc[0]
                    email_t = dados_t.get('Email_Transp', 'Não registado')
                    email_l = dados_t.get('Email_Logistica', 'Não registado')
                    
                    c_t, c_l = st.columns(2)
                    with c_t:
                        st.caption("E-mail Transportadora (Clique na caixa para copiar):")
                        st.code(email_t, language="text") 
                    with c_l:
                        st.caption("E-mail Logística (Clique na caixa para copiar):")
                        st.code(email_l, language="text")
            else: st.info("Aba 'Transportadoras' não encontrada ou ainda está vazia.")
