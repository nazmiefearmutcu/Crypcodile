import subprocess
import json
import time
import pytest
import re

def run_cua(cmd, args, *, required=True):
    full_cmd = ["cua-driver", cmd, json.dumps(args)]
    try:
        res = subprocess.run(full_cmd, capture_output=True, text=True)
    except FileNotFoundError:
        # cua-driver not installed on this host (e.g. CI) -> not a failure.
        pytest.skip("cua-driver binary not available")
    if res.returncode != 0:
        if not required:
            pytest.skip(f"cua-driver '{cmd}' unavailable: {res.stderr.strip()}")
        raise AssertionError(f"CuaDriver command '{cmd}' failed: {res.stderr}")
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return res.stdout

def test_flowmap_gui_cua():
    # Find active window (soft-skip if the cua-driver daemon isn't reachable)
    windows_info = run_cua("list_windows", {}, required=False)
    target_win = None
    for win in windows_info.get("windows", []):
        if "Crypcodile Flowmap Visualizer" in win.get("title", ""):
            target_win = win
            break
            
    if target_win is None:
        pytest.skip("Crypcodile visualizer window not found. Make sure it's running.")
        
    pid = target_win["pid"]
    window_id = target_win["window_id"]
    
    # 1. Snapshot prior to action
    state = run_cua("get_window_state", {
        "pid": pid,
        "window_id": window_id,
        "capture_mode": "som"
    })
    
    tree = state.get("tree_markdown", "")
    assert "Auto-Scroll" in tree
    assert "Symbol:" in tree
    
    # 2. Find Auto-Scroll checkbox index
    match = re.search(r'\[(\d+)\] AXCheckBox "Auto-Scroll"', tree)
    assert match is not None, "Could not find element index for Auto-Scroll"
    auto_scroll_idx = int(match.group(1))
    
    # 3. Toggle Auto-Scroll checkbox
    run_cua("click", {
        "pid": pid,
        "window_id": window_id,
        "element_index": auto_scroll_idx
    })
    
    # Wait for GUI event propagation
    time.sleep(1)
    
    # 4. Snapshot again and verify
    new_state = run_cua("get_window_state", {
        "pid": pid,
        "window_id": window_id,
        "capture_mode": "som"
    })
    new_tree = new_state.get("tree_markdown", "")
    
    # 5. Type a new symbol in the search box
    match_tf = re.search(r'\[(\d+)\] AXTextField', new_tree)
    assert match_tf is not None, "Could not find element index for Search Text Field"
    tf_idx = int(match_tf.group(1))
    
    # Clear and set new value
    run_cua("set_value", {
        "pid": pid,
        "window_id": window_id,
        "element_index": tf_idx,
        "value": "binance-spot:ETHUSDT"
    })
    
    # Commit with Enter
    run_cua("press_key", {
        "pid": pid,
        "window_id": window_id,
        "element_index": tf_idx,
        "key": "return"
    })
    
    # Wait for thread loading & window title update
    time.sleep(2)
    
    # 6. Verify window title/symbol has updated in the final snapshot
    final_state = run_cua("get_window_state", {
        "pid": pid,
        "window_id": window_id,
        "capture_mode": "som"
    })
    
    final_tree = final_state.get("tree_markdown", "")
    assert "ETHUSDT" in final_tree, f"Expected ETHUSDT to be loaded in window, got:\n{final_tree}"
    print("Frontend GUI CuaDriver tests passed successfully!")
