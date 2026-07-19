# Uma Arquitetura Híbrida de Autenticação Biométrica com Detecção de Vivacidade Multicamadas e Integração ao Sistema Operacional Windows

Este artefato apresenta a implementação de um sistema avançado de autenticação para ambientes Windows, proposto no artigo correspondente. O objetivo é demonstrar uma arquitetura híbrida e segura que combina Visão Computacional (para reconhecimento facial e *Liveness Detection* com a rede MiniFASNetV2 via ONNX) operando em um backend Python, um Dashboard Administrativo em Node.js/Electron para gestão de credenciais e auditoria forense, e um *Credential Provider* em C++ para injeção nativa de credenciais no Sistema Operacional. A comunicação entre os módulos isolados ocorre exclusivamente via *Named Pipes* (IPC), mitigando vetores de ataque em rede.

# Estrutura do readme.md

Este repositório está organizado nos seguintes módulos principais:
* `/dashboard`: Código-fonte do frontend administrativo (Node.js/Electron).
* `/src`: Motor de Inteligência Artificial e servidor IPC (Python).
* `/models`: Pesos e arquiteturas de redes neurais pré-treinadas (ONNX).
* `/FaceUnlockProvider`: Código-fonte em C++ do *Credential Provider* nativo do Windows.
* `src/server.py`: Ponto de entrada do backend IA (executar com `python -m src.server` a partir da raiz).
* `requirements.txt` / `package.json`: Mapeamento de dependências.

# Funcionamento do Sistema

O desbloqueio é uma **trava tripla** processada por frame, no `src/server.py`, e só concede
acesso quando as três camadas aprovam:

1. **Identidade (Reconhecimento Facial).** A cada frame o rosto é detectado e convertido em um
   vetor biométrico de 128 dimensões pela CNN ResNet do `face_recognition` (dlib). O vetor é
   comparado com os rostos cadastrados no cofre (`data/faces/`). A ResNet, que é o passo mais
   caro na CPU, só é executada **enquanto** a identidade não é confirmada; depois disso o laço
   fica leve e roda em alta taxa para capturar a vivacidade.
2. **Anti-Spoofing / Textura (Liveness passivo).** Confirmada a identidade, o recorte do rosto é
   enviado à rede **MiniFASNetV2** (ONNX, via `cv2.dnn`), que analisa textura/reflexão para
   distinguir pele viva de um ataque de apresentação (foto/tela). Ver detalhes de
   pré-processamento em *Correção Anti-Spoofing*.
3. **Detecção de Piscar / EAR (Liveness ativo).** Em paralelo, o *Eye Aspect Ratio* dos dois
   olhos é monitorado. Só é aceita uma piscada **genuína**: fechamento **sustentado** dos olhos
   (vários frames abaixo do limiar) seguido de **reabertura** clara. Frames com mais de um rosto
   são descartados (evita o ataque de segurar uma foto ao lado do próprio rosto).

Aprovadas as três camadas, o servidor busca a senha do usuário no cofre (`vault.json`) e a
injeta no Windows via *Named Pipe*. Toda tentativa (sucesso ou bloqueio) é registrada pela
auditoria forense (`logs/`) e no CSV de experimentos.

# Correção Anti-Spoofing e Validação (iBeta Level 1)

Uma auditoria identificou que a camada de textura estava **não-funcional**: com o
pré-processamento anterior, a MiniFASNetV2 respondia ~99% de "real" para **qualquer** entrada
(inclusive ruído e fotos de tela), permitindo desbloqueio com foto de celular. Foram corrigidos
três defeitos em `AntiSpoofingDetector.is_real_skin`:

* **Recorte:** de `+20 px` para **recorte por escala do bounding box (2.7)**, como a arquitetura
  exige (método `_crop_escala`).
* **Normalização:** de `÷255` (que **saturava** a rede) para **pixels crus** (`scale=1.0`, `swapRB=False`).
* **Decisão:** passou a ler a **classe 1 = real** (era a classe 2), com limiar de confiança 0.90.

Adicionalmente, a detecção de piscar foi **endurecida** (fechamento sustentado + reabertura com
amplitude, via histerese, e rejeição de múltiplos rostos), fechando o vetor de foto/impressão
estática.

**Validação.** As correções foram avaliadas no dataset público **iBeta Level 1 Liveness
Detection** (amostras públicas — *paper & screen attacks*), disponível em
<https://www.kaggle.com/datasets/axondata/ibeta-level-1-paper-attacks> (4 vídeos reais e 9 de
ataque; ~60 rostos reais e ~134 de ataque amostrados):

| Métrica | Antes | Depois |
|---|---|---|
| FRR (rosto real rejeitado) | — (tudo passava) | **0,0 %** |
| FAR — ataque de **tela/celular** | 100 % | **0,0 %** |
| FAR — ataque de **papel impresso** | 100 % | 58,9 % (coberto pela camada de piscar) |

A camada de textura corrigida elimina ataques de **tela**; o ataque de **papel** (estático) é
coberto pela camada de **piscar endurecido**, pois uma impressão não produz fechamento ocular
genuíno. O relatório completo, com metodologia e reprodução, está em
[`RELATORIO_ANTISPOOFING.md`](RELATORIO_ANTISPOOFING.md); o script de auditoria em
`reproduzir_auditoria.py`.

> **Nota:** para robustez plena contra papel na própria camada de textura, o caminho é o *par*
> oficial MiniFASNetV2 (2.7) + MiniFASNetV1SE (4.0) do minivision, carregado da fonte oficial
> com `torch.load(weights_only=True)` — registrado como trabalho futuro.

# Selos Considerados

Os selos considerados para avaliação deste artefato pelo CTA do SBSeg são: **Artefatos Disponíveis (SeloD)**, **Artefatos Funcionais (SeloF)**, **Artefatos Sustentáveis (SeloS)** e **Experimentos Reprodutíveis (SeloR)**.

# Informações básicas

Para a execução e replicação plena dos experimentos, o ambiente deve atender aos seguintes requisitos:
* **Sistema Operacional:** Windows 10 ou Windows 11 (Obrigatório devido à arquitetura de *Named Pipes* e à API do *Credential Provider*).
* **Hardware:** Webcam funcional e processador compatível com instruções x64 (para inferência ONNX Runtime em CPU). Recomendado 4GB+ de RAM.
* **Software:** Node.js 18 ou superior e **Python 3.11.x (Obrigatório)**. A versão 3.11 foi homologada estritamente devido à compatibilidade de compilação e estabilidade das bibliotecas base de Visão Computacional e processamento matemático (ONNX/SciPy).
# Dependências

A replicação do ambiente depende das seguintes bibliotecas e frameworks (versões detalhadas nos arquivos de configuração):
* **Backend (Python):** `opencv-python`, `face_recognition`, `scipy`, `onnxruntime` (inferência do MiniFASNetV2). Instaladas via `pip install -r requirements.txt`.
* **Frontend (Node.js):** `electron`, `electron-builder`. Instaladas via `npm install` na pasta `/dashboard`.
* **Recursos de Terceiros:** O modelo de Liveness Detection baseia-se na arquitetura MiniFASNetV2, cujos pesos (`.onnx`) já estão incluídos na pasta `/models` para garantir a execução offline e reprodutível.

# Preocupações com segurança

**ATENÇÃO AVALIADORES:** A execução do *Nível 2* deste artefato (Injeção de Credenciais no SO) envolve a compilação e o registro de uma DLL (`FaceUnlockProvider.dll`) no *Registry* do Windows, alterando a tela de logon (`LogonUI.exe`).
* **Mitigação de Riscos:** Sugere-se fortemente que a avaliação integral (Nível 2) seja realizada em uma Máquina Virtual (VM Windows 10/11) configurada com repasse de USB (*USB Passthrough*) para a webcam, ou em uma máquina física de testes. 
* **Avaliação Segura (Nível 1):** O motor de IA, as defesas Anti-Spoofing e a auditoria do Dashboard (reivindicações primárias do artigo) podem ser testadas integralmente sem a necessidade de instalar a DLL no sistema operacional, apenas rodando o servidor Python e o cliente Electron em nível de usuário.

# Instalação

Siga os passos abaixo para preparar o ambiente (Nível 1 - Seguro):

1. Clone o repositório:
   `git clone https://github.com/JoV1206/Sistema-H-brido-de-Seguran-a-Biom-trica-com-Intelig-ncia-Artificial.git`
2. Configure o Backend (Python):
   Navegue até a raiz do projeto e instale as dependências:
   `pip install -r requirements.txt`
3. Configure o Frontend (Electron):
   Navegue até a pasta `/dashboard` e instale os pacotes Node:
   `cd dashboard`
   `npm install`

# Teste mínimo

Para validar que a instalação foi bem-sucedida e os módulos estão a comunicar via IPC:
1. Em um terminal na raiz do projeto, inicie o servidor IA:
   `python server.py`
   *(Aguarde a mensagem indicando que o Named Pipe está aguardando conexões).*
2. Em um segundo terminal, inicie o Dashboard:
   `cd dashboard`
   `npm start`
3. **Ação esperada:** O painel administrativo abrirá. Defina uma senha mestre. Vá em "Cofre de Usuários" -> "Adicionar Novo Usuário". A webcam será ativada e o rosto será mapeado com sucesso para a pasta local `/data/faces/`.

# Experimentos

Abaixo estão os passos para reproduzir as principais reivindicações de mitigação de ameaças apresentadas no artigo.

## Reivindicação #1: Eficácia do Liveness Detection (Anti-Spoofing)
Esta etapa prova que o sistema não é vulnerável a ataques de apresentação (Spoofing) utilizando fotos.
1. Inicie o `server.py` e o Dashboard Electron.
2. Com um usuário já cadastrado no cofre, efetue o "Logoff" no painel Electron para retornar à tela de bloqueio inicial do Dashboard.
3. Clique em "Desbloquear com Face ID".
4. **Experimento A (Rosto Real):** Posicione seu rosto em frente à câmera e pisque os olhos. O sistema processará a inferência ONNX e concederá o acesso.
5. **Experimento B (Ataque):** Repita o passo 3, mas desta vez aponte a tela de um smartphone exibindo uma foto do rosto cadastrado para a webcam.
6. **Resultado Esperado:** A rede neural MiniFASNetV2 e o rastreio de *blink* identificarão a ausência de vivacidade. O acesso será negado e o painel não abrirá.

## Reivindicação #2: Auditoria Forense e Registro de Intrusões
Esta etapa prova a capacidade do sistema em atuar como ferramenta de resposta a incidentes.
1. No painel de login do Dashboard, tente utilizar o "Desbloquear com Face ID" utilizando o rosto de uma pessoa não cadastrada na base de dados (ou uma foto, como no Experimento 1B).
2. O acesso será negado.
3. Acesse o painel administrativo utilizando a Senha Mestre.
4. Navegue até a aba "Auditoria de Acesso".
5. **Resultado Esperado:** O sistema exibirá um *log* de acesso "NEGADO", contendo a exata data e hora da tentativa, e capturará silenciosamente a fotografia do intruso pela webcam, comprovando a robustez da auditoria local.

# LICENSE

Este projeto está licenciado sob a licença MIT - veja o arquivo [LICENSE](LICENSE) para mais detalhes.