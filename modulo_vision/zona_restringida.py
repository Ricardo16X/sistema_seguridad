from ultralytics import YOLO
import cv2
import numpy as np

model = YOLO("yolov8n.pt")
cap = cv2.VideoCapture(0)

# Define tu zona como lista de puntos (x, y)
# Estos son para una resolución 640x480 — ajústalos a tu gusto
ZONA = np.array([
    [0, 250],
    [800, 250],
    [800, 400],
    [0, 400]
], np.int32)

def punto_en_zona(punto, zona):
    return cv2.pointPolygonTest(zona, punto, False) >= 0

while True:
    ret, frame = cap.read()
    if not ret:
        break

    intruso_detectado = False
    results = model(frame, classes=[0], conf=0.5, verbose=False)

    for box in results[0].boxes:
        x1, y1, x2, y2 = map(int, box.xyxy[0])

        # Punto de los pies (centro inferior del bounding box)
        pie_x = (x1 + x2) // 2
        pie_y = y2

        en_zona = punto_en_zona((pie_x, pie_y), ZONA)

        if en_zona:
            intruso_detectado = True
            # Bounding box ROJO si está en zona
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
            cv2.putText(frame, "INTRUSO EN ZONA", (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        else:
            # Bounding box VERDE si está fuera
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(frame, "Persona", (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        # Punto de los pies visible
        cv2.circle(frame, (pie_x, pie_y), 5, (255, 255, 0), -1)

    # Dibujar la zona (roja si hay intruso, azul si está libre)
    color_zona = (0, 0, 255) if intruso_detectado else (255, 100, 0)
    cv2.polylines(frame, [ZONA], isClosed=True, color=color_zona, thickness=2)

    # Overlay semitransparente en la zona
    overlay = frame.copy()
    cv2.fillPoly(overlay, [ZONA], color_zona)
    cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)

    # Banner de alerta
    if intruso_detectado:
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 40), (0, 0, 255), -1)
        cv2.putText(frame, "  ALERTA: INTRUSO EN ZONA RESTRINGIDA",
                    (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

    cv2.imshow("Zona Restringida - YOLO", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()