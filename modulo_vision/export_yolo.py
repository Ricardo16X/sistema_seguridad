from ultralytics import YOLO
import cv2
import numpy as np
import time
import os



model = YOLO("yolov8n.pt")

# importar modelo onxx
model.export(format="onnx", dynamic=True)