import cv2
import json

def test_camera(index, backend=cv2.CAP_DSHOW, backend_name="DSHOW"):
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        return None
    
    info = {
        "index": index,
        "backend": backend_name,
        "default_width": cap.get(cv2.CAP_PROP_FRAME_WIDTH),
        "default_height": cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
        "default_fps": cap.get(cv2.CAP_PROP_FPS),
        "backend_id": cap.getBackendName(),
    }
    
    # Test common resolutions
    test_res = [
        (640, 480),
        (800, 600),
        (1024, 768),
        (1280, 720),
        (1280, 960),
        (1600, 1200),
        (1920, 1080),
        (2048, 1536),
        (2560, 1440),
        (2592, 1944),
        (3840, 2160),
        (4000, 3000),
    ]
    
    supported_res = []
    for w, h in test_res:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if (actual_w, actual_h) == (w, h):
            ret, frame = cap.read()
            if ret and frame is not None:
                supported_res.append(f"{w}x{h} (verified frame capture {frame.shape[1]}x{frame.shape[0]})")
    
    info["supported_resolutions"] = supported_res
    
    # Test controls
    controls = {
        "AUTO_EXPOSURE": cv2.CAP_PROP_AUTO_EXPOSURE,
        "EXPOSURE": cv2.CAP_PROP_EXPOSURE,
        "AUTOFOCUS": cv2.CAP_PROP_AUTOFOCUS,
        "FOCUS": cv2.CAP_PROP_FOCUS,
        "BRIGHTNESS": cv2.CAP_PROP_BRIGHTNESS,
        "CONTRAST": cv2.CAP_PROP_CONTRAST,
        "SATURATION": cv2.CAP_PROP_SATURATION,
        "HUE": cv2.CAP_PROP_HUE,
        "GAIN": cv2.CAP_PROP_GAIN,
        "SHARPNESS": cv2.CAP_PROP_SHARPNESS,
        "GAMMA": cv2.CAP_PROP_GAMMA,
        "AUTO_WB": cv2.CAP_PROP_AUTO_WB,
        "WB_TEMPERATURE": cv2.CAP_PROP_WB_TEMPERATURE,
        "BACKLIGHT": cv2.CAP_PROP_BACKLIGHT,
        "ZOOM": cv2.CAP_PROP_ZOOM,
    }
    
    ctrl_info = {}
    for name, prop_id in controls.items():
        val = cap.get(prop_id)
        # Try setting
        set_test = False
        if val != -1:
            try:
                # try to toggle or set same val
                res_set = cap.set(prop_id, val)
                ctrl_info[name] = {"value": val, "supported": True, "can_set": res_set}
            except Exception as e:
                ctrl_info[name] = {"value": val, "supported": True, "can_set": False, "error": str(e)}
        else:
            ctrl_info[name] = {"value": -1, "supported": False}
            
    info["controls"] = ctrl_info
    cap.release()
    return info

print("Testing cameras...")
results = []
for idx in range(5):
    res = test_camera(idx, cv2.CAP_DSHOW, "DSHOW")
    if res:
        results.append(res)
    else:
        res_msmf = test_camera(idx, cv2.CAP_MSMF, "MSMF")
        if res_msmf:
            results.append(res_msmf)

print(json.dumps(results, indent=2))
