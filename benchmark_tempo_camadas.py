"""
Benchmark OFFLINE de tempo de inferencia por camada.

Mede, de forma reprodutivel e sem coletar nenhum dado pessoal, o custo
computacional (ms) de cada etapa do pipeline de VERIFY_USER, reutilizando
exatamente as mesmas chamadas do servidor de producao (src/server.py):

    1. Deteccao de rosto (HOG+SVM, dlib)      -> face_recognition.face_locations
    2. Identidade (embedding ResNet-34 128-D)  -> face_recognition.face_encodings
    3. Textura (MiniFASNetV2 / ONNX)           -> AntiSpoofingDetector.is_real_skin
    4. Vivacidade (landmarks 68pts + EAR)      -> face_recognition.face_landmarks + calcular_ear

Entrada: frames amostrados dos videos publicos do iBeta Level 1 (ja consentidos
na criacao do dataset). Nenhuma imagem nova de pessoa e coletada; nenhum dossie
de auditoria (foto/IP/SSID/geo) e gerado -- este script nao aciona o AuditLogger.

Uso (a partir da raiz do projeto, com o venv do servidor):
    .\\venv\\Scripts\\python.exe .\\benchmark_tempo_camadas.py

Saidas:
    dados_tempo_camadas.csv          -> uma linha por medicao (frame x camada)
    dados_tempo_camadas_resumo.json  -> estatisticas agregadas por camada
"""
import os
import sys
import csv
import json
import time
import glob
import platform
from datetime import datetime

import cv2
import numpy as np
import face_recognition

# Reutiliza os artefatos REAIS de producao (nao reimplementa nada).
from src.server import AntiSpoofingDetector, calcular_ear, MODEL_PATH

# ----------------------------------------------------------------------------
# Parametros do benchmark
# ----------------------------------------------------------------------------
DATASET_DIR = os.path.join(
    "DATASET_DAWNLOAD", "iBeta_Level1_Liveness_Detection_dataset"
)
FRAMES_POR_VIDEO = 15   # mesma amostragem declarada no artigo
WARMUP = 5              # 1as medicoes descartadas por camada (aquece cache/ONNX)
# Os videos do iBeta sao 1080x1920 (2 MP). A webcam-alvo do artigo e 720p
# (~0,92 MP). Reduzimos o frame para o maior lado = 1280 (1080x1920 -> 720x1280,
# mesma contagem de pixels de um 1280x720), de modo que o tempo medido represente
# o hardware de operacao, e nao a resolucao (mais alta) do dataset.
MAIOR_LADO_ALVO = 1280
CSV_SAIDA = "dados_tempo_camadas.csv"
JSON_SAIDA = "dados_tempo_camadas_resumo.json"


def redimensionar_720p(frame):
    """Downscale (nunca upscale) para o maior lado == MAIOR_LADO_ALVO, preservando aspecto."""
    h, w = frame.shape[:2]
    m = max(h, w)
    if m <= MAIOR_LADO_ALVO:
        return frame
    s = MAIOR_LADO_ALVO / float(m)
    return cv2.resize(frame, (int(round(w * s)), int(round(h * s))),
                      interpolation=cv2.INTER_AREA)


def amostrar_frames(caminho_video, n):
    """Amostra n frames uniformemente espacados do video (BGR, como o OpenCV entrega)."""
    cap = cv2.VideoCapture(caminho_video)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        cap.release()
        return []
    indices = np.linspace(0, total - 1, num=min(n, total), dtype=int)
    frames = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, frame = cap.read()
        if ok and frame is not None:
            frames.append((int(idx), redimensionar_720p(frame)))
    cap.release()
    return frames


def estatisticas(valores):
    """Estatisticas robustas de uma lista de tempos (ms)."""
    if not valores:
        return None
    a = np.array(valores, dtype=float)
    return {
        "n": int(a.size),
        "media_ms": round(float(a.mean()), 3),
        "desvio_ms": round(float(a.std(ddof=1)) if a.size > 1 else 0.0, 3),
        "mediana_ms": round(float(np.median(a)), 3),
        "min_ms": round(float(a.min()), 3),
        "max_ms": round(float(a.max()), 3),
        "p95_ms": round(float(np.percentile(a, 95)), 3),
    }


def main():
    if not os.path.isdir(DATASET_DIR):
        print(f"[Erro] Dataset nao encontrado em: {DATASET_DIR}")
        sys.exit(1)

    print("[Setup] Carregando MiniFASNetV2 (mesmo modelo do servidor)...")
    spoof = AntiSpoofingDetector(MODEL_PATH)

    videos = sorted(glob.glob(os.path.join(DATASET_DIR, "*", "*.mp4")))
    print(f"[Setup] {len(videos)} videos encontrados no iBeta Level 1.")
    print(f"[Setup] OpenCV {cv2.__version__} | threads OpenCV: {cv2.getNumThreads()} "
          f"| CPU: {platform.processor() or platform.machine()}")

    # Acumuladores por camada (ms), ja descartando warmup.
    tempos = {"deteccao_hog": [], "identidade_embedding": [],
              "textura_onnx": [], "vivacidade_landmarks_ear": []}
    contadores = {k: 0 for k in tempos}  # inclui warmup, p/ saber quantos foram descartados

    linhas_csv = []
    resolucoes = set()
    frames_com_rosto = 0
    frames_processados = 0

    for vpath in videos:
        vetor = os.path.basename(os.path.dirname(vpath))  # REAL / FAKE
        vnome = os.path.basename(vpath)
        for fidx, frame in amostrar_frames(vpath, FRAMES_POR_VIDEO):
            frames_processados += 1
            h, w = frame.shape[:2]
            resolucoes.add((w, h))
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # --- Camada: deteccao HOG (roda todo frame) ---
            t0 = time.perf_counter()
            locs = face_recognition.face_locations(rgb)  # model="hog" default
            dt_hog = (time.perf_counter() - t0) * 1000.0
            contadores["deteccao_hog"] += 1
            if contadores["deteccao_hog"] > WARMUP:
                tempos["deteccao_hog"].append(dt_hog)

            # Downstream so quando ha EXATAMENTE um rosto (como no servidor).
            dt_emb = dt_tex = dt_ear = None
            if len(locs) == 1:
                frames_com_rosto += 1
                loc = locs[0]

                # --- Camada: identidade (embedding ResNet 128-D) ---
                t0 = time.perf_counter()
                _ = face_recognition.face_encodings(rgb, [loc])
                dt_emb = (time.perf_counter() - t0) * 1000.0
                contadores["identidade_embedding"] += 1
                if contadores["identidade_embedding"] > WARMUP:
                    tempos["identidade_embedding"].append(dt_emb)

                # --- Camada: textura (MiniFASNetV2 ONNX) ---
                t0 = time.perf_counter()
                _ = spoof.is_real_skin(frame, loc)
                dt_tex = (time.perf_counter() - t0) * 1000.0
                contadores["textura_onnx"] += 1
                if contadores["textura_onnx"] > WARMUP:
                    tempos["textura_onnx"].append(dt_tex)

                # --- Camada: vivacidade (landmarks 68pts + EAR) ---
                t0 = time.perf_counter()
                lms = face_recognition.face_landmarks(rgb, [loc])
                if lms and "left_eye" in lms[0] and "right_eye" in lms[0]:
                    _ = (calcular_ear(lms[0]["left_eye"]) +
                         calcular_ear(lms[0]["right_eye"])) / 2.0
                dt_ear = (time.perf_counter() - t0) * 1000.0
                contadores["vivacidade_landmarks_ear"] += 1
                if contadores["vivacidade_landmarks_ear"] > WARMUP:
                    tempos["vivacidade_landmarks_ear"].append(dt_ear)

            linhas_csv.append([
                vetor, vnome, fidx, w, h, len(locs),
                round(dt_hog, 3),
                round(dt_emb, 3) if dt_emb is not None else "",
                round(dt_tex, 3) if dt_tex is not None else "",
                round(dt_ear, 3) if dt_ear is not None else "",
            ])
        print(f"  - {vetor}/{vnome}: {frames_processados} frames ate aqui", flush=True)

    # --- Escreve CSV detalhado ---
    with open(CSV_SAIDA, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["Vetor", "Video", "Frame_idx", "Largura", "Altura",
                     "Rostos_detectados", "HOG_ms", "Embedding_ms",
                     "Textura_ms", "Landmarks_EAR_ms"])
        wr.writerows(linhas_csv)

    # --- Agrega e escreve resumo ---
    resumo = {
        "gerado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ambiente": {
            "cpu": platform.processor() or platform.machine(),
            "opencv": cv2.__version__,
            "opencv_threads": cv2.getNumThreads(),
            "python": platform.python_version(),
            "inferencia": "CPU (DNN_TARGET_CPU)",
        },
        "protocolo": {
            "dataset": "iBeta Level 1 (amostras publicas)",
            "videos": len(videos),
            "frames_por_video": FRAMES_POR_VIDEO,
            "frames_processados": frames_processados,
            "frames_com_um_rosto": frames_com_rosto,
            "warmup_descartado_por_camada": WARMUP,
            "resolucoes_frame": sorted([f"{w}x{h}" for (w, h) in resolucoes]),
        },
        "tempos_por_camada_ms": {k: estatisticas(v) for k, v in tempos.items()},
    }

    # tempo total por frame cooperativo = soma das medias das 4 camadas
    medias = [resumo["tempos_por_camada_ms"][k]["media_ms"]
              for k in tempos if resumo["tempos_por_camada_ms"][k]]
    resumo["tempo_total_por_frame_ms_estimado"] = round(float(sum(medias)), 3)

    with open(JSON_SAIDA, "w", encoding="utf-8") as f:
        json.dump(resumo, f, indent=2, ensure_ascii=False)

    # --- Imprime resumo legivel ---
    print("\n" + "=" * 64)
    print("RESUMO -- tempo de inferencia por camada (CPU, ms)")
    print("=" * 64)
    rotulos = {
        "deteccao_hog": "Deteccao (HOG+SVM)",
        "identidade_embedding": "Identidade (embedding 128-D)",
        "textura_onnx": "Textura (MiniFASNetV2/ONNX)",
        "vivacidade_landmarks_ear": "Vivacidade (landmarks+EAR)",
    }
    for k in tempos:
        s = resumo["tempos_por_camada_ms"][k]
        if s:
            print(f"{rotulos[k]:<34} media={s['media_ms']:>7.2f}  "
                  f"dp={s['desvio_ms']:>6.2f}  mediana={s['mediana_ms']:>7.2f}  "
                  f"p95={s['p95_ms']:>7.2f}  n={s['n']}")
    print("-" * 64)
    print(f"Tempo total por frame cooperativo (soma das medias): "
          f"{resumo['tempo_total_por_frame_ms_estimado']:.2f} ms")
    print(f"Frames com 1 rosto: {frames_com_rosto}/{frames_processados}  |  "
          f"resolucoes: {resumo['protocolo']['resolucoes_frame']}")
    print(f"\nArquivos: {CSV_SAIDA} , {JSON_SAIDA}")


if __name__ == "__main__":
    main()
