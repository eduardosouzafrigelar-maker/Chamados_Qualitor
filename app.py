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
        # Usa .worksheets() que é mais estável que .get_worksheet()
        todas_abas = sh.worksheets()
        
        if len(todas_abas) >= 2:
            aba_chamados = todas_abas[0] # Pega a 1ª
            aba_users = todas_abas[1]    # Pega a 2ª
            break # Sucesso! Sai do loop
        else:
            erro_real = "A planilha tem menos de 2 abas visíveis."
            
    except Exception as e:
        erro_real = str(e)
        # Espera progressiva: 2s, 3s, 4s... para dar tempo ao Google
        time.sleep(2 + tentativa) 

# SE FALHOU TUDO: Mostra o erro real para consertarmos
if aba_chamados is None or aba_users is None:
    st.error("❌ O Robô desistiu depois de 10 tentativas.")
    st.warning(f"O motivo exato do erro foi: {erro_real}")
    
    if "429" in erro_real:
        st.info("Isso é bloqueio de velocidade do Google. Espere 1 minuto.")
    elif "API key not valid" in erro_real:
        st.info("Verifique suas credenciais.")
    
    if st.button("Tentar conectar novamente agora"):
        st.rerun()
    st.stop()

# --- CACHE DE DADOS ---
@st.cache_data(ttl=10) # Aumentei o cache para 10s para evitar bater no Google toda hora
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
        st.warning("Carregando dados... Se travar, clique no botão abaixo.")
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

    # --- CENÁRIO A: TEM CHAMADO ---
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
        
        if st.button("✅ FINALIZAR", type="primary"):
            try:
                st.cache_data.clear()
                
                cell = aba_chamados.find(str(id_linha))
                linha = cell.row
                agora = hora_brasil()
                
                aba_chamados.update_cell(linha, 3, "Concluido")
                aba_chamados.update_cell(linha, 6, agora)
                
                st.success("Feito!")
                time.sleep(1)
                st.rerun()
            except Exception as e:
                st.error(f"Erro ao finalizar: {e}")

    # --- CENÁRIO B: LIVRE ---
    else:
        pendentes = df[df['Status'] == 'Pendente']
        qtd = len(pendentes)

        st.metric("Fila", qtd)

        if qtd > 0:
            if st.button("📥 PEGAR PRÓXIMO"):
                # Limpa cache IMEDIATAMENTE antes de tentar pegar
                st.cache_data.clear()
                
                try:
                    # Busca manual para garantir que ninguém pegou
                    # (Não usamos a função com cache aqui propositalmente)
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



