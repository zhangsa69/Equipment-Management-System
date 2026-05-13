import sys

def recover_encoding(input_path, output_path):
    try:
        # Read the corrupted file as UTF-8 (how it was saved)
        with open(input_path, 'r', encoding='utf-8-sig') as f:
            corrupted_content = f.read()
        
        # Best effort recovery:
        # For each character, try to encode to GBK and back to UTF-8
        # We process it line by line or block by block to handle errors
        recovered_lines = []
        for line in corrupted_content.splitlines():
            try:
                # This is the "magic" undo:
                # 1. Encode the mis-decoded string to GBK bytes
                # 2. Decode those bytes as UTF-8
                recovered_line = line.encode('gbk', errors='replace').decode('utf-8', errors='replace')
                
                # Fix some common artifacts or remaining broken chars
                # (Manual fixes based on project context)
                recovered_line = recovered_line.replace('妫€鏌ユ棩蹇', '检查日志')
                recovered_line = recovered_line.replace('妫€鏌ヨ鍒', '检查计划')
                recovered_line = recovered_line.replace('缁存姢璁″垝', '维护计划')
                recovered_line = recovered_line.replace('璧勪骇绠＄悊', '资产管理')
                recovered_line = recovered_line.replace('鍏ㄥ眬姒傝', '全局概览')
                recovered_line = recovered_line.replace('浜哄憳閰嶇疆', '人员配置')
                recovered_line = recovered_line.replace('娌冲寳浜溅', '河北京车')
                recovered_line = recovered_line.replace('璁惧绠＄悊', '设备管理')
                recovered_line = recovered_line.replace('涓昏彍鍗', '主菜单')
                recovered_line = recovered_line.replace('浠〃鐩', '仪表盘')
                recovered_line = recovered_line.replace('璁惧鍒楄〃', '设备列表')
                recovered_line = recovered_line.replace('鏂板璁惧', '新增设备')
                recovered_line = recovered_line.replace('鎵批噺瀵煎叆', '批量导入')
                recovered_line = recovered_line.replace('鎼滅储璁惧', '搜索设备')
                recovered_line = recovered_line.replace('璁惧鍚嶇О', '设备名称')
                recovered_line = recovered_line.replace('璁惧缂栧彿', '设备编号')
                recovered_line = recovered_line.replace('瑙勬牸鍨嬪彿', '规格型号')
                recovered_line = recovered_line.replace('璐熻矗閮ㄩ棬', '负责部门')
                recovered_line = recovered_line.replace('鐘舵€', '状态')
                recovered_line = recovered_line.replace('鎿嶄綔', '操作')
                recovered_line = recovered_line.replace('ID', 'ID') # Should be fine
                
                recovered_lines.append(recovered_line)
            except Exception as e:
                recovered_lines.append(line) # Fallback to original if total failure
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(recovered_lines))
        
        print(f"Successfully processed {len(recovered_lines)} lines.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    recover_encoding('dashboard.html', 'dashboard_fixed.html')
