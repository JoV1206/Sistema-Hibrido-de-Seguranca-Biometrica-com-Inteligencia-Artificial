"""
Captura e rotula frames da webcam para validar a camada anti-spoofing.

Uso (a partir da raiz do projeto, com o mesmo Python do servidor):
    .\venv\Scripts\python.exe .\capturar_validacao.py

Controles (com a janela de video em foco):
    R  -> salva o frame atual como ROSTO REAL
    A  -> salva o frame atual como ATAQUE (foto no celular / impressa)
    Q  -> sair

Dica de qualidade: capture com BOA LUZ. Para os ATAQUES, mostre a foto do
rosto cadastrado na tela do celular (ou impressa) preenchendo o quadro, em
distancias/angulos variados. Meta: ~10 reais + ~10 ataques.
"""
import os
import cv2

REAL_DIR = os.path.join('data', 'validacao', 'real')
ATK_DIR = os.path.join('data', 'validacao', 'ataque')
os.makedirs(REAL_DIR, exist_ok=True)
os.makedirs(ATK_DIR, exist_ok=True)


def proximo_indice(pasta, prefixo):
    n = 0
    for f in os.listdir(pasta):
        if f.startswith(prefixo) and f.endswith('.jpg'):
            try:
                n = max(n, int(f[len(prefixo):-4]))
            except ValueError:
                pass
    return n + 1


cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
if not cap.isOpened():
    raise SystemExit('Nao consegui abrir a webcam (indice 0). Feche outros apps que a usem.')

n_real = len([f for f in os.listdir(REAL_DIR) if f.endswith('.jpg')])
n_atk = len([f for f in os.listdir(ATK_DIR) if f.endswith('.jpg')])
print(f'Iniciando. Ja existem {n_real} reais e {n_atk} ataques.')
print('R = real  |  A = ataque  |  Q = sair')

try:
    while True:
        ok, frame = cap.read()
        if not ok:
            print('Falha ao ler o frame.')
            break

        hud = frame.copy()
        cv2.putText(hud, f'REAIS: {n_real}   ATAQUES: {n_atk}', (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.putText(hud, 'R=real  A=ataque  Q=sair', (10, 65),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.imshow('Captura de Validacao', hud)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('r'):
            idx = proximo_indice(REAL_DIR, 'real_')
            caminho = os.path.join(REAL_DIR, f'real_{idx:03d}.jpg')
            cv2.imwrite(caminho, frame)
            n_real += 1
            print(f'[REAL]   salvo {caminho}')
        elif key == ord('a'):
            idx = proximo_indice(ATK_DIR, 'ataque_')
            caminho = os.path.join(ATK_DIR, f'ataque_{idx:03d}.jpg')
            cv2.imwrite(caminho, frame)
            n_atk += 1
            print(f'[ATAQUE] salvo {caminho}')
finally:
    cap.release()
    cv2.destroyAllWindows()
    print(f'\nFinalizado. Total: {n_real} reais, {n_atk} ataques em data/validacao/')
