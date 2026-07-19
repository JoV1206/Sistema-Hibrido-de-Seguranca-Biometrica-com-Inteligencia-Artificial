# ==================================================================================================================================================== #        
# Author: João Vitor Almeida Teodoro                                                                                                                   #
# Project: Uma Arquitetura Híbrida de Autenticação Biométrica com Detecção de Vivacidade Multicamadas e Integração ao Sistema Operacional Windows      #
# ==================================================================================================================================================== #

import time
import re
import win32pipe
import win32file
import pywintypes
import cv2
import numpy as np
import os
import csv
from scipy.spatial import distance as dist

import face_recognition
from src.camera import Camera
from src.recognizer import FaceRecognizer

import requests
import subprocess
from datetime import datetime
import json

PIPE_NAME = r'\\.\pipe\FaceUnlockPipe'
MODEL_PATH = r'C:\FaceUnlock\RECONHECIMENTOFACIAL_WINDOWS\models\minifasnetv2.onnx'

def chave_segura(texto):
    """
    Espelha a sanitização do Dashboard (safeName em main.js: [^a-zA-Z0-9] -> _).
    A identidade reconhecida vem do NOME DO ARQUIVO da foto (já sanitizado), mas o
    vault.json é gravado com o nome original (com acentos/espaços). Normalizamos
    para que os dois casem sem precisar recadastrar o usuário.
    """
    return re.sub(r'[^a-zA-Z0-9]', '_', texto)

# =======================================================================
# MATEMÁTICA DO PISCAR (EAR)
# =======================================================================
def calcular_ear(olho):
    """
    Calcula o Eye Aspect Ratio (EAR) utilizando a distância euclidiana entre os 
    pontos fiduciais do olho. Esta é a 3ª camada do sistema (Rastreio Dinâmico),
    exigindo uma resposta em tempo real do usuário para mitigar ataques com máscaras 
    estáticas ou fotos de altíssima resolução que passem pelo filtro DNN.
    """
    A = dist.euclidean(olho[1], olho[5])
    B = dist.euclidean(olho[2], olho[4])
    C = dist.euclidean(olho[0], olho[3])
    return (A + B) / (2.0 * C)

# =======================================================================
# DETECTOR DE TEXTURA (DNN)
# =======================================================================
class AntiSpoofingDetector:
    """
    Módulo de Presentation Attack Detection (PAD). Utiliza a arquitetura MiniFASNetV2
    compilada em formato ONNX para inferência ultrarrápida em CPU local.
    Analisa a textura, profundidade e reflexão da pele para diferenciar um rosto real 
    de um ataque de apresentação (foto impressa ou tela de dispositivo).
    """
    def __init__(self, model_path):
        if not os.path.exists(model_path):
            print(f"[Erro Crítico] Arquivo de modelo não encontrado em: {model_path}")
            exit(1)
        self.net = cv2.dnn.readNetFromONNX(model_path)
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        print("[Sistema] Cérebro Anti-Spoofing (DNN) carregado.")

    # Recorte por escala do bounding box, como exige o MiniFASNet (Silent-Face).
    # NÃO é o "+20px" antigo: o modelo precisa da face centralizada com contexto
    # proporcional (escala 2.7). Fora disso a rede satura e responde constante.
    ESCALA_CROP = 2.7
    # Limiar da classe "real" validado no dataset iBeta Level 1 (ver
    # RELATORIO_ANTISPOOFING.md): com 0.90, FRR~0% e FAR=0% em ataques de tela.
    LIMIAR_REAL = 0.90

    def _crop_escala(self, frame, face_location):
        """Reproduz CropImage do Silent-Face: centraliza no rosto e expande por escala."""
        top, right, bottom, left = face_location
        H, W, _ = frame.shape
        x, y, bw, bh = left, top, right - left, bottom - top
        s = min((H - 1) / bh, (W - 1) / bw, self.ESCALA_CROP)
        nw, nh = bw * s, bh * s
        cx, cy = x + bw / 2.0, y + bh / 2.0
        lx, ly = cx - nw / 2.0, cy - nh / 2.0
        rx, ry = cx + nw / 2.0, cy + nh / 2.0
        if lx < 0: rx -= lx; lx = 0
        if ly < 0: ry -= ly; ly = 0
        if rx > W - 1: lx -= (rx - W + 1); rx = W - 1
        if ry > H - 1: ly -= (ry - H + 1); ry = H - 1
        return frame[int(ly):int(ry) + 1, int(lx):int(rx) + 1]

    def is_real_skin(self, frame, face_location):
        try:
            face_bgr = self._crop_escala(frame, face_location)
            if face_bgr.size == 0:
                return False, 0

            # Pixels CRUS (scale=1.0, sem /255) e BGR (swapRB=False): é a
            # distribuição que este .onnx espera. Com /255 a rede satura.
            blob = cv2.dnn.blobFromImage(face_bgr, 1.0, (80, 80), (0, 0, 0), swapRB=False, crop=False)
            self.net.setInput(blob)
            preds = self.net.forward()

            logits = preds[0]
            exp_logits = np.exp(logits - np.max(logits))
            probabilidades = exp_logits / np.sum(exp_logits)

            # Classe 1 = rosto real (0 e 2 = ataque), confirmado empiricamente no dataset.
            prob_real = probabilidades[1]
            pct_real = prob_real * 100

            is_real = (np.argmax(probabilidades) == 1 and prob_real >= self.LIMIAR_REAL)
            return is_real, pct_real

        except Exception as e:
            return False, 0

# =======================================================================
# MÓDULO DE AUDITORIA E TELEMETRIA (STEALTH LOGGER)
# =======================================================================
class AuditLogger:
    """
    Ferramenta de resposta a incidentes forenses. Captura dados do invasor no momento
    de um falso positivo evitado (bloqueio de spoofing). Opera de forma furtiva para 
    armazenar localmente um dossiê JSON com IP, SSID, e vetor facial da tentativa.
    """
    def __init__(self, log_dir="logs"):
        self.log_dir = log_dir
        # Cria a pasta secreta de logs se ela não existir
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)

    def get_wifi_ssid(self):
        """Usa comandos nativos do Windows para descobrir o Wi-Fi atual."""
        try:
            output = subprocess.check_output(['netsh', 'wlan', 'show', 'interfaces']).decode('utf-8', errors='ignore')
            for line in output.split('\n'):
                if "SSID" in line and "BSSID" not in line:
                    return line.split(':')[1].strip()
        except Exception:
            pass
        return "Desconhecido/Cabeado"

    def get_location(self):
        """Faz um ping rápido e invisível para pegar o IP e a Localização."""
        try:
            # Timeout de 2 segundos para não travar o login do Windows
            res = requests.get('https://ipinfo.io/json', timeout=2).json()
            return res.get('ip', 'N/A'), res.get('city', 'N/A'), res.get('loc', 'N/A')
        except Exception:
            return "Offline", "N/A", "N/A"

    def registrar_evento(self, frame, status, motivo, usuario="Desconhecido", vetor_facial=None):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nome_base = f"{timestamp}_{status}"

        # 1. Salva a foto silenciosamente
        caminho_foto = os.path.join(self.log_dir, f"{nome_base}.jpg")
        if frame is not None:
            cv2.imwrite(caminho_foto, frame)

        # 2. Coleta a Telemetria
        ssid = self.get_wifi_ssid()
        ip, cidade, coords = self.get_location()

        # 3. Monta o Dossiê
        dossie = {
            "data_hora": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "status": status,
            "motivo": motivo,
            "usuario_identificado": usuario,
            "rede_wifi": ssid,
            "ip_publico": ip,
            "cidade": cidade,
            "coordenadas_gps": coords,
            "foto_arquivo": f"{nome_base}.jpg",
            "vetor_facial": vetor_facial # NOVO CAMPO
        }

        # 4. Salva o JSON no disco
        caminho_json = os.path.join(self.log_dir, f"{nome_base}.json")
        with open(caminho_json, 'w', encoding='utf-8') as f:
            json.dump(dossie, f, indent=4, ensure_ascii=False)

# =======================================================================
# COLETOR DE DADOS CIENTÍFICOS (GERAÇÃO DE DATASET PARA O ARTIGO)
# =======================================================================
def registrar_teste_csv(alvo, tipo_teste, camadas_ativas, tempo_ms, resultado, motivo):
    """
    Função para registro automático de dados experimentais. Tabula o 
    FAR (False Acceptance Rate), FRR (False Rejection Rate) e o tempo 
    computacional em milissegundos para viabilizar a análise estatística e gráfica.
    """
    arquivo_csv = 'dados_experimentos_sbseg.csv'
    cabecalho = ['Timestamp', 'Alvo', 'Tipo_Teste', 'Camadas_Ativas', 'Tempo_ms', 'Resultado', 'Motivo_Bloqueio']
    
    existe = os.path.exists(arquivo_csv)
    
    try:
        with open(arquivo_csv, mode='a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            if not existe:
                writer.writerow(cabecalho) # Escreve o cabeçalho na primeira vez
            
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow([timestamp, alvo, tipo_teste, camadas_ativas, round(tempo_ms, 2), resultado, motivo])
    except Exception as e:
        print(f"[Erro no Log CSV] {e}")

# =======================================================================
# SERVIDOR PRINCIPAL (HÍBRIDO + COFRE SEGURO + AUDITORIA)
# =======================================================================
def run_server():
    """
    Motor principal isolado via Inter-Process Communication (IPC - Named Pipes).
    Impede que a interface administrativa ou o LogonUI do SO processem vetores
    sensíveis, protegendo contra vazamentos de memória e injeção de código.
    """
    print("=== Servidor Biométrico HÍBRIDO Iniciado ===")
    recognizer = FaceRecognizer()
    spoof_detector = AntiSpoofingDetector(MODEL_PATH)
    auditoria = AuditLogger() # <-- Instanciamos o cão de guarda!
    
    # --- VARIÁVEIS DO COFRE ---
    tentativas_falhas = 0
    MAX_TENTATIVAS = 5
    tempo_bloqueio = 0
    TEMPO_DE_CASTIGO = 120 
    
    while True:
        print(f"\n[Escuta] Criando Named Pipe: {PIPE_NAME}")
        pipe = win32pipe.CreateNamedPipe(
            PIPE_NAME, win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
            1, 65536, 65536, 0, None
        )
        
        try:
            win32pipe.ConnectNamedPipe(pipe, None)
            hr, data = win32file.ReadFile(pipe, 1024)
            message = data.decode('utf-8').strip('\x00')
            
            if "VERIFY_USER" in message:
                
                if tentativas_falhas >= MAX_TENTATIVAS:
                    if time.time() < tempo_bloqueio:
                        # Bloqueado: Não acende a câmera, mas registra o evento
                        auditoria.registrar_evento(None, "BLOQUEADO", "Tentativa durante Lockout")
                        win32file.WriteFile(pipe, b"ACCESS_DENIED")
                        win32pipe.DisconnectNamedPipe(pipe)
                        win32file.CloseHandle(pipe)
                        continue 
                    else:
                        print("[Segurança] 🟢 Tempo de bloqueio expirado.")
                        tentativas_falhas = 0 

                print("[IA] Iniciando Trava Dupla...")
                
                # ⏱️ INÍCIO DO CRONÔMETRO CIENTÍFICO (Para métrica do Artigo)
                tempo_inicio_metrica = time.time() 

                # O processo de autenticação híbrida é dividido em 3 etapas:
                # 1. Reconhecimento Facial (Identidade)
                # 2. Detecção de Textura (Anti-Spoofing)
                # 3. Detecção de Piscar (Liveness)

                with Camera() as cam:
                    time.sleep(0.3)  # Tempo para a câmera ajustar a luz (evita fotos escuras ou superexpostas no início)
                    
                    timeout = 10.0  # Tempo máximo para o processo de autenticação (em segundos), ou seja, o usuário tem 10 segundos para piscar após a identificação facial. Isso evita loops infinitos e acelera o processo.
                    start_time = time.time()
                    
                    identidade_ok = False  # Primeiro passo: Reconhecimento Facial (Identidade)
                    textura_ok = False     # Segundo passo: Detecção de Textura (Anti-Spoofing)
                    piscou_ok = False      # Terceiro passo: Detecção de Piscar (Liveness)
                    nome_usuario = ""
                    ultimo_frame_capturado = None # <-- Guarda a foto
                    
                    frames_reais = 0
                    # --- Piscar ENDURECIDO (liveness ativo) ---
                    # Dois limiares com histerese: o olho precisa ficar ABAIXO de
                    # EAR_FECHADO por vários frames (fechamento SUSTENTADO) e depois
                    # subir ACIMA de EAR_ABERTO (reabertura). A folga entre os dois
                    # exige amplitude real: uma foto de olhos abertos (~0.30) nunca
                    # cruza EAR_FECHADO, e tremor de foto raramente sustenta o fechamento.
                    EAR_ABERTO = 0.25          # olho considerado aberto
                    EAR_FECHADO = 0.18         # olho considerado fechado
                    MIN_FRAMES_FECHADO = 2     # fechamento tem de durar >= 2 frames
                    frames_fechados = 0        # contador de frames consecutivos com olho fechado
                    media_ear = 0.30    # Última média de EAR (inicia "aberto" p/ evitar NameError no print)
                    pct_real = 0.0      # Última confiança de "pele real" retornada pela DNN

                    # Variável para guardar o vetor matemático do rosto
                    ultimo_vetor_capturado = None

                    while time.time() - start_time < timeout:
                        frame = cam.get_frame()
                        ultimo_frame_capturado = frame.copy() # <-- Tira a cópia para a auditoria

                        # O OpenCV entrega o frame em BGR, mas o face_recognition (dlib) foi
                        # treinado em RGB. Convertemos UMA vez por frame e reutilizamos.
                        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        face_locations = face_recognition.face_locations(rgb_frame)

                        if len(face_locations) == 0:
                            continue                                                 #<-- Sem rosto no frame: não gasta a ResNet à toa

                        if len(face_locations) > 1:
                            # Mais de um rosto em quadro num desbloqueio individual é
                            # suspeito (ex.: invasor segurando uma foto ao lado do próprio
                            # rosto). Não avança identidade nem liveness enquanto durar.
                            print("[Segurança] Múltiplos rostos no quadro — frame ignorado.")
                            continue

                        localizacao = face_locations[0]

                        # === ETAPA 1: IDENTIDADE ===
                        # A ResNet de 128-D é o passo mais caro na CPU. Só a executamos
                        # ENQUANTO a identidade não foi confirmada; depois disso o loop
                        # fica leve e roda em alta taxa para capturar o piscar (liveness).
                        if not identidade_ok:
                            encodings = face_recognition.face_encodings(rgb_frame, [localizacao])
                            if len(encodings) > 0:
                                ultimo_vetor_capturado = encodings[0].tolist()       #<-- Guarda o vetor facial para a auditoria
                                is_match, result = recognizer.verify_encoding(encodings[0])
                                if is_match:
                                    identidade_ok = True          #<-- Identidade confirmada, agora vamos para os próximos testes de segurança
                                    nome_usuario = result         #<-- O nome do usuário reconhecido (ou ID) para a auditoria

                        # === ETAPAS 2 e 3: ANTI-SPOOFING (textura) + LIVENESS (piscar) ===
                        if identidade_ok:                                            #<-- Só valida vivacidade depois de saber QUEM é
                            # A DNN de textura faz o recorte por escala 2.7 e usa pixels crus (BGR).
                            is_real, pct_real = spoof_detector.is_real_skin(frame, localizacao)
                            if is_real:                                       #<-- Se a textura for real, incrementa os frames reais consecutivos
                                frames_reais += 1
                                if frames_reais >= 1: textura_ok = True
                            else:
                                frames_reais = 0
                                textura_ok = False

                            # Landmarks reaproveitando a localização já detectada (não redetecta).
                            landmarks_list = face_recognition.face_landmarks(rgb_frame, [localizacao])
                            marcas = landmarks_list[0] if landmarks_list else {}

                            if 'left_eye' in marcas and 'right_eye' in marcas:       #<-- Se tiver as marcas dos olhos, calcula o EAR para detectar o piscar
                                ear_esq = calcular_ear(marcas['left_eye'])
                                ear_dir = calcular_ear(marcas['right_eye'])
                                media_ear = (ear_esq + ear_dir) / 2.0                #<-- Média dos dois olhos (detecção mais robusta)

                                # Piscar válido = fechamento SUSTENTADO seguido de reabertura clara.
                                # Uma foto/tela estática não produz um fechamento genuíno; o tremor
                                # de uma imagem raramente mantém o olho fechado por vários frames.
                                if media_ear <= EAR_FECHADO:
                                    frames_fechados += 1                             #<-- Olho fechado: acumula duração
                                elif media_ear >= EAR_ABERTO:
                                    if frames_fechados >= MIN_FRAMES_FECHADO:        #<-- Reabriu após fechar de verdade -> piscada real
                                        piscou_ok = True
                                    frames_fechados = 0                             #<-- Reseta ao reabrir (zona intermediária mantém a contagem)

                            print(f"[Status] Textura: {pct_real:05.1f}% | EAR: {media_ear:.2f} | Piscou: {piscou_ok}")

                            if textura_ok and piscou_ok:
                                break

                    # FIM DO CRONÔMETRO
                    tempo_total_ms = (time.time() - tempo_inicio_metrica) * 1000

                    # --- DECISÃO FINAL COM AUDITORIA E LOG ---
                    if identidade_ok and textura_ok and piscou_ok:
                        print(f"[IA] ✅ ✅ ACESSO LIBERADO: {nome_usuario}")
                        tentativas_falhas = 0
                        
                        # Salva o log de SUCESSO na Auditoria
                        auditoria.registrar_evento(ultimo_frame_capturado, "SUCESSO", "Autenticação Híbrida Aprovada", nome_usuario, ultimo_vetor_capturado)
                        
                        # Salva na Planilha do Experimento (SBSeg)
                        registrar_teste_csv(nome_usuario, "Acesso Realizado", "Vetor+ONNX+Blink", tempo_total_ms, "Concedido", "Nenhum")
                        
                        try:
                            with open('vault.json', 'r', encoding='utf-8') as f:
                                cofre = json.load(f)

                            # Procura a senha casando o nome reconhecido (vindo do arquivo
                            # da foto, já sanitizado) com as chaves do vault, que podem estar
                            # com acentos/espaços. Aceita match direto ou via sanitização.
                            senha_do_usuario = ""
                            for chave, senha in cofre.items():
                                if chave == nome_usuario or chave_segura(chave) == nome_usuario:
                                    senha_do_usuario = senha
                                    break

                            if senha_do_usuario != "":
                                mensagem_final = f"ACCESS_GRANTED|{senha_do_usuario}"
                                response = mensagem_final.encode('utf-8')
                            else:
                                print(f"[Aviso] Rosto aprovado ({nome_usuario}), mas sem senha correspondente no vault.json.")
                                response = b"ACCESS_DENIED"
                        except FileNotFoundError:
                            response = b"ACCESS_DENIED"
                            
                    else:
                        tentativas_falhas += 1
                        print(f"[IA] ❌ ❌ ACESSO NEGADO. (Tentativa {tentativas_falhas} de {MAX_TENTATIVAS})")
                        
                        # Verifica se a IA reconheceu o rosto, mesmo que o liveness (piscar) tenha falhado
                        nome_invasor = nome_usuario if identidade_ok else "Desconhecido"
                        
                        # Salva o log de INVASÃO na Auditoria
                        motivo_falha = f"Id:{identidade_ok} | Tex:{textura_ok} | Pisc:{piscou_ok}"
                        auditoria.registrar_evento(ultimo_frame_capturado, "NEGADO", motivo_falha, nome_invasor, ultimo_vetor_capturado)
                        
                        # Salva na Planilha do Experimento (SBSeg)
                        registrar_teste_csv(nome_invasor, "Ataque/Falha", "Vetor+ONNX+Blink", tempo_total_ms, "Negado", motivo_falha)
                        
                        if tentativas_falhas >= MAX_TENTATIVAS:
                            tempo_bloqueio = time.time() + TEMPO_DE_CASTIGO
                            
                        response = b"ACCESS_DENIED"
                        
                    win32file.WriteFile(pipe, response)

            elif "MANUAL_ENTRY" in message:
                print("[Auditoria] 🚨 Alerta de Digitação Manual! Capturando foto rápida...")
                try:
                    with Camera() as cam:
                        time.sleep(0.3) # Tempo mínimo para a lente ajustar a luz
                        frame = cam.get_frame()
                        
                        # Usa a nossa classe de auditoria para salvar o evento
                        auditoria.registrar_evento(frame, "ALERTA", "Login via Teclado (Senha Manual digitada)", "Não Biométrico", None)
                except Exception as e:
                    print(f"[Erro Auditoria] Falha ao capturar foto manual: {e}")

        except pywintypes.error as e:
            print(f"[Erro de Comunicação] Falha no pipe: {e}")
            
        except Exception as e:
            # Se qualquer coisa der errado (ex: câmara em uso, erro matemático), 
            # ele não crasha, apenas nega o acesso e avisa o erro!
            print(f"[Erro Fatal na IA] {e}")
            try:
                win32file.WriteFile(pipe, b"ACCESS_DENIED|ERRO_INTERNO")
            except Exception:
                pass
                
        finally:
            # Fecha o cano limpo para a próxima tentativa
            try:
                win32pipe.DisconnectNamedPipe(pipe)
                win32file.CloseHandle(pipe)
            except Exception:
                pass

if __name__ == '__main__':
    run_server()