import time
import numpy as np
import cv2
from ultralytics import YOLO
import platform
import os

MODEL_PATH = "yolov8n.onnx"
RUNS       = 50
WARMUP     = 5

CONFIGS = [
    {"imgsz": 192, "label": "imgsz=192 (mínimo)"},
    {"imgsz": 256, "label": "imgsz=256"},
    {"imgsz": 320, "label": "imgsz=320 (actual)"},
    {"imgsz": 416, "label": "imgsz=416"},
    {"imgsz": 640, "label": "imgsz=640 (estándar)"},
]

SEP  = "─" * 58
SEP2 = "═" * 58


def synthetic_frame(w=1280, h=720):
    frame = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)
    cv2.rectangle(frame, (300, 200), (600, 600), (200, 200, 200), -1)
    return frame


def run_config(model, cfg, frame):
    for _ in range(WARMUP):
        model.track(frame, classes=[0], conf=0.65,
                    persist=False, verbose=False, imgsz=cfg["imgsz"])

    times = []
    for _ in range(RUNS):
        t0 = time.perf_counter()
        model.track(frame, classes=[0], conf=0.65,
                    persist=False, verbose=False, imgsz=cfg["imgsz"])
        times.append((time.perf_counter() - t0) * 1000)

    arr   = np.array(times)
    p95   = np.percentile(arr, 95)
    fps   = 1000 / arr.mean()
    skip1 = 1000 / (arr.mean() / 2)

    return {
        "label":  cfg["label"],
        "mean":   arr.mean(),
        "min":    arr.min(),
        "max":    arr.max(),
        "p95":    p95,
        "fps":    fps,
        "skip1":  skip1,
        "stable": fps >= 15,
    }


def print_system_info():
    print(SEP2)
    print("  BENCHMARK — YOLOv8n ONNX · CPU Only")
    print(SEP2)
    print(f"  SO:       {platform.system()} {platform.release()}")
    print(f"  Python:   {platform.python_version()}")
    print(f"  CPU:      {platform.processor() or 'AMD Ryzen 3 5300U'}")
    try:
        import onnxruntime as ort
        print(f"  ORT:      {ort.__version__}  providers={ort.get_available_providers()}")
    except ImportError:
        pass
    print(SEP2)


def print_results(results):
    print(f"\n{'Config':<28} {'Lat(ms)':>8} {'P95(ms)':>8} {'FPS':>6} {'1en2':>6}  {'OK':>4}")
    print(SEP)
    for r in results:
        ok = "✓" if r["stable"] else "✗"
        print(f"  {r['label']:<26} {r['mean']:>7.1f} {r['p95']:>8.1f} "
              f"{r['fps']:>6.1f} {r['skip1']:>6.1f}  {ok:>4}")
    print(SEP)
    print("  Lat(ms) = latencia media por frame")
    print("  P95     = percentil 95 (latencia pico)")
    print("  FPS     = frames por segundo procesando todo")
    print("  1en2    = FPS efectivos saltando 1 de cada 2 frames")
    print("  ✓       = >= 15 FPS (apto para vigilancia)")


def print_recommendation(results):
    aptos = [r for r in results if r["stable"]]
    print(f"\n{'RECOMENDACIÓN':─^58}")
    if aptos:
        best = max(aptos, key=lambda r: r["fps"])
        sweet = min(aptos, key=lambda x: abs(x["fps"] - 20))
        print(f"  Configuración óptima : {sweet['label']}")
        print(f"  FPS efectivos        : {sweet['fps']:.1f} (sin skip)  "
              f"{sweet['skip1']:.1f} (1en2)")
        print(f"  Más rápido posible   : {best['label']}  ({best['fps']:.1f} FPS)")
    else:
        slowest = results[-1]
        print("  Ninguna config alcanza 15 FPS estables.")
        print(f"  El mejor resultado fue {slowest['fps']:.1f} FPS con {slowest['label']}.")
        print("  Considera reducir la resolución de captura a 640x480.")
    print(SEP)


def main():
    print_system_info()

    if not os.path.exists(MODEL_PATH):
        print(f"[ERROR] No se encontró {MODEL_PATH}")
        print("        Ejecuta primero: python export_yolo.py")
        return

    print(f"  Cargando modelo: {MODEL_PATH}")
    model = YOLO(MODEL_PATH)
    frame = synthetic_frame()
    print(f"  Frame sintético: {frame.shape[1]}x{frame.shape[0]}")
    print(f"  Runs por config: {WARMUP} warmup + {RUNS} medición")
    print()

    results = []
    for i, cfg in enumerate(CONFIGS, 1):
        print(f"  [{i}/{len(CONFIGS)}] {cfg['label']} ...", end=" ", flush=True)
        r = run_config(model, cfg, frame)
        results.append(r)
        print(f"{r['mean']:.1f}ms  {r['fps']:.1f} FPS")

    print_results(results)
    print_recommendation(results)


if __name__ == "__main__":
    main()
