"""
Script de auditoria (somente leitura) da camada anti-spoofing.
Acompanha RELATORIO_ANTISPOOFING.md. NAO altera o sistema — apenas roda o
modelo em entradas controladas para evidenciar que ele nao discrimina.

Uso (a partir da raiz do projeto):
    .\venv\Scripts\python.exe .\reproduzir_auditoria.py
"""
import cv2, numpy as np, face_recognition

MODEL = r'models\MiniFASNetV2.onnx'
net = cv2.dnn.readNetFromONNX(MODEL)


def sm(p):
    e = np.exp(p - np.max(p))
    return e / np.sum(e)


def infer(face_bgr, norm=1/255., swap=True):
    blob = cv2.dnn.blobFromImage(face_bgr, norm, (80, 80), (0, 0, 0), swapRB=swap, crop=False)
    net.setInput(blob)
    return sm(net.forward()[0])


def crop_mais20(img):
    """Reproduz o recorte usado em AntiSpoofingDetector.is_real_skin."""
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    locs = face_recognition.face_locations(rgb)
    if not locs:
        return None
    t, r, b, l = locs[0]
    h, w, _ = img.shape
    return img[max(0, t-20):min(h, b+20), max(0, l-20):min(w, r+20)]


# 1) entradas sinteticas -> tudo vira ~99,4% "real"
print("== Sinteticas (nao sao rosto) ==")
for nome, im in {
    "cinza":  np.full((200, 200, 3), 128, np.uint8),
    "ruido":  np.random.randint(0, 255, (200, 200, 3), np.uint8),
    "branco": np.full((200, 200, 3), 255, np.uint8),
}.items():
    p = infer(im)
    print(f"  {nome:6s} real={p[2]*100:5.1f}%  is_real={p.argmax() == 2 and p[2] >= 0.99}")

# 2) ataque (celular) vs real
print("== Amostras rotuladas (logs/) ==")
rotulos = {
    "ATAQUE 230145": r"logs\20260713_230145_SUCESSO.jpg",
    "REAL   224833": r"logs\20260713_224833_SUCESSO.jpg",
    "REAL   230059": r"logs\20260713_230059_SUCESSO.jpg",
}
for k, f in rotulos.items():
    img = cv2.imread(f)
    if img is None:
        print(f"  {k}: arquivo ausente ({f})")
        continue
    c = crop_mais20(img)
    if c is None:
        print(f"  {k}: sem rosto detectado")
        continue
    a = infer(c, 1/255.)
    b = infer(c, 1.0)
    print(f"  {k}: 1/255->{a[2]*100:5.1f}%(cls{a.argmax()}) | 1.0->{b[2]*100:5.1f}%(cls{b.argmax()})")
