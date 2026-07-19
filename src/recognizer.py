import os
import cv2
import face_recognition
import numpy as np
from src.config import FACES_DIR, TOLERANCE

class FaceRecognizer:
    def __init__(self):
        self.known_face_encodings = []
        self.known_face_names = []
        self.last_update = 0  # Controla quando a pasta foi modificada
        self.load_faces()

    def check_for_updates(self):
        """
        Sincronia em Tempo Real: Verifica se o Painel Electron salvou ou 
        apagou fotos novas antes de fazer a verificação biométrica.
        """
        if not os.path.exists(FACES_DIR):
            return
        
        # Pega o tempo da última modificação na pasta
        current_mod_time = os.path.getmtime(FACES_DIR)
        if current_mod_time > self.last_update:
            print("\n[Sistema] Alteração detectada no cofre de rostos. Sincronizando IA com o Painel...")
            self.load_faces()
            self.last_update = current_mod_time

    def load_faces(self):
        """Lê as fotos (.jpg/.png) salvas pelo Dashboard e converte em vetores matemáticos."""
        self.known_face_encodings = []
        self.known_face_names = []
        
        if not os.path.exists(FACES_DIR):
            os.makedirs(FACES_DIR)
        
        for filename in os.listdir(FACES_DIR):
            if filename.lower().endswith((".jpg", ".jpeg", ".png")):
                name = os.path.splitext(filename)[0]
                filepath = os.path.join(FACES_DIR, filename)
                
                try:
                    # Carrega a imagem JPG salva pelo Electron
                    image = face_recognition.load_image_file(filepath)
                    face_locations = face_recognition.face_locations(image)
                    
                    if face_locations:
                        # Converte a foto em vetor biométrico (128 dimensões)
                        encoding = face_recognition.face_encodings(image, face_locations)[0]
                        self.known_face_encodings.append(encoding)
                        self.known_face_names.append(name)
                    else:
                        print(f"[Aviso] A foto {filename} não contém um rosto legível e foi ignorada.")
                except Exception as e:
                    print(f"[Erro] Falha ao processar {filename}: {e}")
                    
        print(f"[Sistema] {len(self.known_face_names)} rosto(s) em formato de imagem sincronizado(s) na memória.")

    def verify_encoding(self, face_encoding: np.ndarray) -> tuple[bool, str]:
        """
        Compara um vetor facial JÁ extraído (128-D) com os vetores salvos.
        Separado de verify_face para que o servidor possa extrair o encoding
        uma única vez por frame e evitar recomputar a ResNet (custo alto na CPU).
        """
        # 1. Garante que a memória está idêntica ao que o Electron mostra na tela
        self.check_for_updates()

        if not self.known_face_encodings:
            return False, "Nenhum rosto cadastrado no banco de dados."

        # Compara com os rostos conhecidos usando a tolerância definida no config.py
        matches = face_recognition.compare_faces(self.known_face_encodings, face_encoding, tolerance=TOLERANCE)
        face_distances = face_recognition.face_distance(self.known_face_encodings, face_encoding)

        if len(face_distances) == 0:
            return False, "Erro ao calcular distância biométrica."

        # Pega o rosto com a menor "distância matemática" (o mais parecido)
        best_match_index = np.argmin(face_distances)
        if matches[best_match_index]:
            name = self.known_face_names[best_match_index]
            return True, name

        return False, "Rosto não reconhecido."

    def verify_face(self, frame: np.ndarray) -> tuple[bool, str]:
        """Detecta e compara o rosto atual (frame BGR do OpenCV) com os vetores salvos."""

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        face_locations = face_recognition.face_locations(rgb_frame)

        if not face_locations:
            self.check_for_updates()
            return False, "Nenhum rosto encontrado na câmera."

        # Pega apenas o primeiro rosto que aparecer na câmera para tentar o login
        face_encoding = face_recognition.face_encodings(rgb_frame, face_locations)[0]
        return self.verify_encoding(face_encoding)