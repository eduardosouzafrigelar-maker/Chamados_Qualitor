import streamlit as st
import pandas as pd
import gspread
from datetime import datetime
import time
import pytz

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(page_title="Distribuidor Qualitor", page_icon="🎫")

# --- CONEXÃO BÁSICA ---
@st.cache_resource
def conectar_google_sheets():
    try:
        if "gcp_service_account" in st.secrets:
            creds_dict = st.secrets["gcp_service_account"]
            client = gspread.service_account_from_dict(creds_dict)
        else:
            client = gspread.service_account(filename="credentials.json")
        
        return client.open("Chamados_Qualitor")
    except Exception as e:
        return None

# --- FUNÇÃO HORA BRASIL ---
def hora_brasil():
    fuso = pytz.timezone('America/Sao_Paulo')
    return datetime.now(fuso).strftime("%d/%m/%Y %H:%M:%S")

# --- O ROBÔ ZEN (CARREGAMENTO DAS ABAS) ---
sh = conectar_google_sheets()
aba_chamados = None
aba_users = None
erro_real = ""

if sh is None:
    st.error("Erro total: Não consegui nem abrir a planilha.")
    st.stop()

# Tenta 10 vezes (paciência total de ~40 segundos)
for tentativa in range(10):
    try:
        todas_abas = sh.worksheets()
        
        if len(todas_abas) >= 2:
            aba_chamados = todas_abas[0] # Pega a 1ª
            aba_users = todas_abas[1]    # Pega a 2ª
            break 
        else:
            erro_real = "A planilha tem menos de 2 abas visíveis."
            
    except Exception as e:
        erro_real = str(e)
        time.sleep(2 + tentativa) 

# SE FALHOU TUDO
if aba_chamados is None or aba_users is None:
    st.error("❌ O Robô desistiu depois de 10 tentativas.")
    st.warning(f"Motivo: {erro_real}")
    if st.button("Tentar conectar novamente agora"):
        st.rerun()
    st.stop()

# --- CACHE DE DADOS ---
@st.cache_data(ttl=10)
def carregar_dados_planilha():
    try:
        dados = aba_chamados.get_all_records()
        return pd.DataFrame(dados)
    except:
        return pd.DataFrame()

# --- TELA DE LOGIN ---
if 'usuario' not in st.session_state:
    st.title("🎫 ESTEIRA - QUALITOR")
    try:
        lista_nomes = aba_users.col_values(1)[1:] 
    except:
        lista_nomes = []
    
    escolha = st.selectbox("Selecione seu nome:", [""] + lista_nomes)
    
    if st.button("Entrar no Sistema"):
        if escolha:
            st.session_state['usuario'] = escolha
            st.rerun()
        else:
            st.warning("Selecione um nome.")

# --- TELA PRINCIPAL ---
else:
    usuario = st.session_state['usuario']
    
    with st.sidebar:
        st.write(f"Logado como: **{usuario}**")
        if st.button("Sair / Trocar Usuário"):
            del st.session_state['usuario']
            st.rerun()
    
    st.title(f"Olá, {usuario} 👋")
    st.divider()

    df = carregar_dados_planilha()

    if df.empty:
        st.warning("Carregando dados...")
        if st.button("🔄 Forçar Recarregamento"):
            st.cache_data.clear()
            st.rerun()
        st.stop()

    if 'Status' in df.columns and 'Responsavel' in df.columns:
        meu_chamado = df[
            (df['Status'] == 'Em Andamento') & 
            (df['Responsavel'] == usuario)
        ]
    else:
        st.error("Erro: Colunas 'Status' ou 'Responsavel' não encontradas.")
        st.stop()

    # --- CENÁRIO A: TEM CHAMADO (AGORA COM CONFIRMAÇÃO) ---
    if not meu_chamado.empty:
        dados = meu_chamado.iloc[0]
        numero_chamado = dados.get('Dados', 'N/A') 
        id_linha = dados.get('ID')
        
        st.info(f"Pendência: **{numero_chamado}**")
        
        col1, col2 = st.columns([3, 1])
        with col1:
            if numero_chamado != 'N/A':
                link = f"https://frigelar.qualitorsoftware.com/html/hd/hdchamado/cadastro_chamado.php?cdchamado={numero_chamado}"
                st.link_button("🔗 ABRIR QUALITOR", link)
        
        st.write("---")
        
        # --- AQUI COMEÇA A LÓGICA DA CONFIRMAÇÃO ---
        
        # Cria a variável de memória se ela não existir
        if 'confirmar_fim' not in st.session_state:
            st.session_state['confirmar_fim'] = False

        # SE NÃO ESTIVER CONFIRMANDO, MOSTRA O BOTÃO NORMAL
        if not st.session_state['confirmar_fim']:
            if st.button("✅ FINALIZAR ATENDIMENTO", type="primary"):
                st.session_state['confirmar_fim'] = True # Ativa o modo confirmação
                st.rerun()
        
        # SE ESTIVER CONFIRMANDO, MOSTRA O AVISO E OS BOTÕES SIM/NÃO
        else:
            st.warning(f"⚠️ **Tem certeza que deseja finalizar o chamado {numero_chamado}?**")
            col_sim, col_nao = st.columns(2)
            
            with col_sim:
                if st.button("👍 SIM, FINALIZAR", type="primary", use_container_width=True):
                    try:
                        st.cache_data.clear()
                        
                        cell = aba_chamados.find(str(id_linha))
                        linha = cell.row
                        agora = hora_brasil()
                        
                        aba_chamados.update_cell(linha, 3, "Concluido")
                        aba_chamados.update_cell(linha, 6, agora)
                        
                        st.success("Feito!")
                        st.session_state['confirmar_fim'] = False # Reseta a memória
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erro ao finalizar: {e}")
            
            with col_nao:
                if st.button("❌ NÃO / CANCELAR", use_container_width=True):
                    st.session_state['confirmar_fim'] = False # Cancela e volta ao normal
                    st.rerun()

    # --- CENÁRIO B: LIVRE ---
    else:
        # Garante que o estado de confirmação esteja desligado se você não tiver chamado
        st.session_state['confirmar_fim'] = False 
        
        pendentes = df[df['Status'] == 'Pendente']
        qtd = len(pendentes)

        st.metric("Fila", qtd)

        if qtd > 0:
            if st.button("📥 PEGAR PRÓXIMO"):
                st.cache_data.clear()
                
                try:
                    dados_reais = aba_chamados.get_all_records()
                    df_novo = pd.DataFrame(dados_reais)
                    
                    fila = df_novo[
                        (df_novo['Status'] == 'Pendente') & 
                        (df_novo['Responsavel'] == "")
                    ]
                    
                    if not fila.empty:
                        primeiro = fila.iloc[0]
                        id_chamado = primeiro['ID']
                        
                        cell = aba_chamados.find(str(id_chamado))
                        linha = cell.row
                        agora = hora_brasil()
                        
                        aba_chamados.update_cell(linha, 3, "Em Andamento")
                        aba_chamados.update_cell(linha, 4, usuario)
                        aba_chamados.update_cell(linha, 5, agora)
                        
                        st.toast("Chamado atribuído!")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.warning("Alguém pegou antes!")
                        time.sleep(2)
                        st.rerun()
                except Exception as e:
                    st.error(f"Erro ao pegar: {e}")
        else:
            st.success("Fila zerada!")
            if st.button("🔄 Verificar"):
                st.cache_data.clear()
                st.rerun()



