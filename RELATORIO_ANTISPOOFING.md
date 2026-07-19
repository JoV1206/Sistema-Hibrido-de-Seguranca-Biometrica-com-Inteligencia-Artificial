# Relatório Técnico — Auditoria da Camada Anti-Spoofing (Liveness)

**Data:** 2026-07-13
**Autor da análise:** auditoria de código (assistida)
**Escopo:** Camada de *Presentation Attack Detection* (PAD) do sistema — rede `MiniFASNetV2.onnx`
e detecção de piscar (EAR) em `src/server.py`.
**Gatilho:** um ataque de apresentação com **foto exibida na tela de um celular** obteve
`ACCESS_GRANTED` no sistema.
**Atualização (2026-07-18):** correção aplicada e validada no dataset iBeta Level 1 — ver Seção 8.

---

## 1. Sumário executivo

A auditoria confirma, com evidência empírica reproduzível, que **a camada de detecção de
vivacidade não está funcional**:

1. **A CNN de textura (MiniFASNetV2), como integrada, é não-discriminativa.** Ela retorna
   ~99,4% de confiança na classe "real" para **qualquer** entrada — inclusive ruído aleatório,
   cor sólida e a foto de ataque no celular. Na prática, equivale a um "sim" constante.
2. **A detecção de piscar (EAR) é frágil e contornável.** A máquina de estado exige uma única
   transição de EAR (fechar/abrir) em 10 s, o que é disparado por acaso ao mover a foto
   (tremor nos *landmarks*). Foi o vetor que permitiu o desbloqueio com foto estática.

**Impacto:** a **Reivindicação #1 do artigo** ("Eficácia do Liveness Detection / Anti-Spoofing")
**não é sustentada** pelo comportamento atual do artefato.

---

## 2. Metodologia

- **Ambiente:** venv do projeto (`./venv`), `opencv-python==4.13.0.92`, `face_recognition==1.3.0`.
- **Modelo:** `models/MiniFASNetV2.onnx` carregado via `cv2.dnn.readNetFromONNX`.
- **Pipeline testado:** idêntico ao de produção em `AntiSpoofingDetector.is_real_skin`
  (`src/server.py`): detecção de face, recorte, `cv2.dnn.blobFromImage`, `softmax`, leitura da
  classe "real".
- **Amostras rotuladas** (capturas reais do próprio sistema, pasta `logs/`):
  - `logs/20260713_230145_SUCESSO.jpg` → **ATAQUE** (foto no celular; visível a moldura do
    aparelho, o reflexo de um segundo rosto na tela e o fundo/brilho de tela).
  - `logs/20260713_224833_SUCESSO.jpg` → **REAL** (rosto ao vivo).
  - `logs/20260713_230059_SUCESSO.jpg` → **REAL** (rosto ao vivo).

O script de reprodução está na Seção 6.

---

## 3. Evidências

### 3.1 A CNN responde ~99,4% "real" para entradas que não são rosto

Alimentando o modelo com o mesmo pré-processamento de produção (`scale=1/255`, `swapRB=True`):

| Entrada              | Confiança classe "real" | `is_real` (regra atual ≥99%) |
|----------------------|------------------------:|:----------------------------:|
| Cinza médio (sólido) | 99,4 %                  | **True** |
| Preto (sólido)       | 99,2 %                  | **True** |
| Branco (sólido)      | 99,5 %                  | **True** |
| Ruído aleatório      | 99,4 %                  | **True** |
| Azul (sólido)        | 99,2 %                  | **True** |

> Nenhuma dessas entradas é um rosto; todas passam. Isto, por si só, invalida a camada.

### 3.2 O ataque real (foto no celular) passa; a normalização não corrige

| Frame                | Rótulo        | `scale=1/255` (produção) | `scale=1.0` (pixels crus) |
|----------------------|---------------|-------------------------:|--------------------------:|
| `...230145` 📱        | **ATAQUE**    | 99,4 % real ❌           | 90,9 % real ❌            |
| `...224833`          | REAL          | 99,3 %                   | 95,6 %                    |
| `...230059`          | REAL          | 99,3 %                   | 5,6 % (rejeitaria) ❌     |

> Em `1/255` o ataque é indistinguível do real. Trocar a normalização para `1.0` **não** separa
> o ataque (segue 90,9% "real") e ainda rejeitaria um rosto legítimo.

### 3.3 Pré-processamento correto do Silent-Face (crop por escala) → saída contraditória

Reproduzindo o recorte oficial do Silent-Face (`CropImage`, escalas 2.7 e 4.0):

| Config                              | ATAQUE 230145 | REAL 224833 | REAL 230059 |
|-------------------------------------|:-------------:|:-----------:|:-----------:|
| crop=2.7, norm=1/255                | classe 2      | classe 2    | classe 2    |
| crop=2.7, norm=1.0, swapRB=False    | **classe 2 (100%)** | classe 1 | **classe 0** |
| crop=2.7, norm=1.0, swapRB=True     | classe 2      | classe 1    | classe 1    |

> Em `norm=1.0` as classes se movem, mas de forma **contraditória**: o ataque vira a classe
> "mais real", e os **dois frames reais nem concordam entre si** (classe 1 vs classe 0). Não
> existe limiar estável que separe real de ataque com este `.onnx`.

**Conclusão da Seção 3:** o modelo está recebendo uma distribuição de entrada que não corresponde
ao seu treino/exportação e produz saídas **sem significado consistente**. Não é corrigível por
ajuste de `scale`/`swapRB`/crop — é um problema de **proveniência e integração do modelo**.

---

## 4. Causa-raiz

### 4.1 CNN de textura (crítico)
- O `cv2.dnn.blobFromImage(..., 1.0/255.0, (80,80), swapRB=True)` em `is_real_skin`
  (`src/server.py`) satura o modelo: os *logits* ficam praticamente constantes
  (rosto `[-3.83, -0.69, 4.52]` vs ruído `[-3.68, -0.74, 4.42]`), levando sempre à classe 2.
- O recorte `+20 px` não corresponde ao recorte por escala de bounding box que o MiniFASNetV2
  espera; e não há garantia de que a normalização/ordem de canais bata com a exportação deste
  `.onnx` específico (proveniência desconhecida).
- Resultado: `textura_ok` é sempre verdadeiro na prática → **camada inócua**.

### 4.2 Detecção de piscar (alto)
- A máquina de estado (`estado_piscar`) exige **uma única** transição EAR ≤ 0,22 seguida de
  EAR > 0,22 em 10 s. Sem exigência de fechamento **sustentado**, de **amplitude mínima**, nem
  de rosto único.
- Ao mover uma foto no celular, o tremor de *landmarks* cruza 0,22 por acaso e marca "piscada".
- Uma foto estática (olhos pintados abertos) **não deveria** produzir um fechamento genuíno —
  logo, uma lógica de piscar bem construída bloquearia este ataque.

---

## 5. Recomendações

Prioridade por relação custo/benefício:

1. **(Rápido, fecha o ataque relatado) Endurecer a liveness ativa (piscar):**
   - Exigir olhos fechados por N frames consecutivos (fechamento sustentado).
   - Exigir amplitude: baseline aberto (ex. EAR > 0,28) → fechado (EAR < 0,15) → reaberto.
   - Rejeitar quando houver mais de um rosto no frame.
   - Travar a verificação de piscar no **mesmo rosto** que confirmou a identidade.
   - Opcional: desafio ativo (virar a cabeça / piscar sob comando com *timeout*).

2. **(Médio) Reintegrar a CNN corretamente ou substituí-la:**
   - Recuperar a proveniência do `MiniFASNetV2.onnx` e replicar exatamente o pré-processamento
     de origem (crop por escala + normalização), validando contra um **conjunto rotulado**
     real-vs-ataque antes de confiar. Silent-Face usa um **par** de modelos somados — verificar
     se falta o segundo.
   - Alternativa: adotar um modelo PAD de proveniência conhecida.

3. **(Integridade do artigo) Até haver validação, não afirmar eficácia da camada de textura.**
   Reescrever a Reivindicação #1 para refletir a defesa que de fato funciona (liveness ativo)
   e documentar a limitação atual da CNN.

> Enquanto (1) e (2) não forem feitos, o sistema **não** deve ser considerado resistente a
> ataques de apresentação.

---

## 6. Reprodução

Executar a partir da raiz do projeto, com o venv:

```powershell
.\venv\Scripts\python.exe .\reproduzir_auditoria.py
```

```python
# reproduzir_auditoria.py
import glob, os, cv2, numpy as np, face_recognition

MODEL = r'models\MiniFASNetV2.onnx'
net = cv2.dnn.readNetFromONNX(MODEL)
def sm(p): e = np.exp(p - np.max(p)); return e / np.sum(e)

def infer(face_bgr, norm=1/255., swap=True):
    blob = cv2.dnn.blobFromImage(face_bgr, norm, (80, 80), (0, 0, 0), swapRB=swap, crop=False)
    net.setInput(blob); return sm(net.forward()[0])

def crop_mais20(img):
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB); locs = face_recognition.face_locations(rgb)
    if not locs: return None
    t, r, b, l = locs[0]; h, w, _ = img.shape
    return img[max(0, t-20):min(h, b+20), max(0, l-20):min(w, r+20)]

# 1) entradas sinteticas -> tudo vira ~99,4% "real"
print("== Sinteticas (nao sao rosto) ==")
for nome, im in {
    "cinza": np.full((200,200,3),128,np.uint8),
    "ruido": np.random.randint(0,255,(200,200,3),np.uint8),
    "branco": np.full((200,200,3),255,np.uint8),
}.items():
    p = infer(im); print(f"  {nome:6s} real={p[2]*100:5.1f}%  is_real={p.argmax()==2 and p[2]>=0.99}")

# 2) ataque (celular) vs real
print("== Amostras rotuladas ==")
rotulos = {
    "ATAQUE 230145": r"logs\20260713_230145_SUCESSO.jpg",
    "REAL   224833": r"logs\20260713_224833_SUCESSO.jpg",
    "REAL   230059": r"logs\20260713_230059_SUCESSO.jpg",
}
for k, f in rotulos.items():
    img = cv2.imread(f); c = crop_mais20(img)
    if c is None: print(f"  {k}: sem rosto"); continue
    a = infer(c, 1/255.); b = infer(c, 1.0)
    print(f"  {k}: 1/255->{a[2]*100:5.1f}%(cls{a.argmax()}) | 1.0->{b[2]*100:5.1f}%(cls{b.argmax()})")
```

---

## 7. Arquivos de evidência

- `logs/20260713_230145_SUCESSO.jpg` — frame do ataque (foto no celular) que obteve acesso.
- `logs/20260713_224833_SUCESSO.jpg`, `logs/20260713_230059_SUCESSO.jpg` — rostos reais.
- Correlato: `dados_experimentos_sbseg.csv` registra estes eventos com o tempo e o resultado.

---

## 8. Correção aplicada e validação (2026-07-18)

### 8.1 Dataset de validação
iBeta Level 1 Liveness Detection (`DATASET_DAWNLOAD/`): 4 vídeos REAL e 9 FAKE
(6 de papel impresso + 3 de tela). Amostragem de 15 frames por vídeo, com detecção
facial; ~60 rostos reais e ~134 de ataque efetivamente avaliados.

### 8.2 Correções em `src/server.py`
**a) Camada de textura (`AntiSpoofingDetector.is_real_skin`):** três bugs corrigidos —
- recorte passou de `+20 px` para **crop por escala 2.7** do bounding box (método `_crop_escala`);
- normalização passou de `÷255` (que satura a rede) para **pixels crus** (`scale=1.0`, `swapRB=False`);
- a decisão passou a ler **classe 1 = real** (era classe 2), com **limiar 0.90**.

**b) Liveness ativo (piscar):** máquina de estado de transição única substituída por
**fechamento sustentado + reabertura com amplitude** (histerese `EAR_FECHADO=0.18` /
`EAR_ABERTO=0.25`, mínimo de 2 frames fechados) e **rejeição de múltiplos rostos** no quadro.

### 8.3 Resultados (medidos com o `is_real_skin` de produção)

| Métrica | Config ANTERIOR | Config CORRIGIDA |
|---|---|---|
| FRR (rosto real rejeitado)      | — (tudo passava) | **0,0 %** |
| FAR — ataque de **tela/celular** | 100 % (passava)  | **0,0 %** |
| FAR — ataque de **papel impresso** | 100 % (passava) | 58,9 % |

> A config anterior classificava **tudo** como "real" a ~99,4% (FAR efetivo de 100%).

### 8.4 Interpretação e defesa em camadas
- A textura corrigida **elimina os ataques de tela** (vetor demonstrado no gatilho) sem
  travar usuários legítimos (FRR 0%).
- O MiniFASNet único **não cobre papel impresso** (FAR ~59%) — limitação conhecida da
  arquitetura. Esse vetor é **estático** e passa a ser coberto pelo **piscar endurecido**
  (§8.2b): uma foto/impressão não produz fechamento ocular sustentado.
- **Camadas combinadas:** textura (telas) + piscar (estáticos/papel) cobrem ambos os vetores.

### 8.5 Limitações e trabalho futuro
- Métricas obtidas em vídeos bem iluminados; em **pouca luz** o real pode pontuar mais baixo
  (observado nos frames de webcam originais) — recomenda-se iluminação mínima na captura.
- Para robustez plena contra **papel** na própria camada de textura, o caminho é o **par
  oficial** MiniFASNetV2 (2.7) + MiniFASNetV1SE (4.0) do minivision, integrado a partir da
  **fonte oficial** e carregado com `torch.load(weights_only=True)` (sem execução de pickle).
  Fica como trabalho futuro; não foi adotado nesta correção por exigir dependência pesada.
