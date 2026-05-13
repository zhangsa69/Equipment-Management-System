import os
import shutil

def replace_project(root_dir, repo_dir):
    backup_dir = os.path.join(root_dir, 'backup_corrupted')
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)
    
    # List of files/folders to preserve in root
    preserve = ['temp_repo', 'backup_corrupted', 'device_mvp_v3.db', 'device_mvp.db']
    
    # Step 1: Backup current files
    for item in os.listdir(root_dir):
        if item in preserve:
            continue
        
        src = os.path.join(root_dir, item)
        dst = os.path.join(backup_dir, item)
        
        try:
            if os.path.exists(dst):
                if os.path.isdir(dst):
                    shutil.rmtree(dst)
                else:
                    os.remove(dst)
            shutil.move(src, backup_dir)
            print(f"Backed up {item}")
        except Exception as e:
            print(f"Failed to backup {item}: {e}")

    # Step 2: Copy from repo_dir to root_dir
    for item in os.listdir(repo_dir):
        if item == '.git':
            continue
            
        src = os.path.join(repo_dir, item)
        dst = os.path.join(root_dir, item)
        
        try:
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
            print(f"Restored {item}")
        except Exception as e:
            print(f"Failed to restore {item}: {e}")

if __name__ == "__main__":
    replace_project(r'H:\设备管理', r'H:\设备管理\temp_repo')
