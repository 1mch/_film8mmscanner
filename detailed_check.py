import cv2
from pygrabber.dshow_graph import FilterGraph

graph = FilterGraph()
devices = graph.get_input_devices()
print("DirectShow Input Devices:")
for i, name in enumerate(devices):
    print(f"Index {i}: {name}")

print("\n--- Detailed Testing ---")
for idx, name in enumerate(devices):
    print(f"\n================ Device {idx}: {name} ================")
    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print("Could not open device.")
        continue

    # Test formats: MJPG vs YUYV
    for fourcc_str in ['MJPG', 'YUY2']:
        fourcc = cv2.VideoWriter_fourcc(*fourcc_str)
        cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        actual_fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        actual_fourcc_str = "".join([chr((actual_fourcc >> 8 * i) & 0xFF) for i in range(4)])
        print(f"FourCC test {fourcc_str} -> set result FourCC: {actual_fourcc_str}")

    # Test top resolutions & FPS
    res_list = [
        (3840, 2160),
        (2560, 1440),
        (1920, 1080),
        (1280, 720),
        (640, 480)
    ]
    for w, h in res_list:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        act_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        act_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        ret, frame = cap.read()
        if ret and frame is not None:
            print(f"Resolution {w}x{h} -> OK (actual: {act_w}x{act_h}, frame captured: {frame.shape[1]}x{frame.shape[0]}, reported FPS: {fps})")
            # Save sample thumbnail/image for highest working resolution
            if (act_w, act_h) == (w, h):
                fname = f"sample_cam{idx}_{w}x{h}.jpg"
                cv2.imwrite(fname, frame)
                print(f"  Saved test frame to {fname}")
        else:
            print(f"Resolution {w}x{h} -> Failed to capture (got {act_w}x{act_h})")

    cap.release()
