import os
import shutil
import time
import sys
from datetime import datetime
from dotenv import load_dotenv
from smb.SMBConnection import SMBConnection
import ping3

# 加载配置文件（和脚本同目录的.env文件）
load_dotenv("config.env")

# ===================== 配置项（已通过.env文件管理，无需改这里） =====================
SOURCE_FOLDERS = [folder.strip() for folder in os.getenv("SOURCE_FOLDERS", "").split(",")]
BACKUP_TYPE = os.getenv("BACKUP_TYPE", "")
LOCAL_LAN_PATH = os.getenv("LOCAL_LAN_PATH", "")
SMB_SERVER_IP = os.getenv("SMB_SERVER_IP", "")
SMB_SERVER_PORT = int(os.getenv("SMB_SERVER_PORT", 139))
SMB_USERNAME = os.getenv("SMB_USERNAME", "")
SMB_PASSWORD = os.getenv("SMB_PASSWORD", "")
SMB_SHARE_NAME = os.getenv("SMB_SHARE_NAME", "")
LOG_FILE = os.getenv("LOG_FILE", "lan_backup_log.txt")
PING_RETRY = int(os.getenv("PING_RETRY", 5))
PING_INTERVAL = int(os.getenv("PING_INTERVAL", 10))

def write_log(message):
    """写入备份日志（带时间戳，方便排查问题）"""
    log_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_content = f"[{log_time}] {message}\n"
    # 写入日志文件
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_content)
    # 同时打印到控制台
    print(log_content.strip())

def check_lan_connectivity(ip):
    """检查网线连接的目标IP是否可达（核心：确保局域网连通）"""
    write_log(f"🔍 开始检查局域网连通性（目标IP：{ip}）")
    for retry in range(PING_RETRY):
        try:
            delay = ping3.ping(ip, timeout=2)  # ping超时2秒
            if delay is not None and delay > 0:
                write_log(f"✅ 局域网连通性检查通过（延迟：{delay:.2f}秒）")
                return True
            else:
                write_log(f"❌ 第{retry+1}次检查失败：IP不可达，{PING_INTERVAL}秒后重试")
                time.sleep(PING_INTERVAL)
        except Exception as e:
            write_log(f"❌ 第{retry+1}次检查出错：{str(e)}，{PING_INTERVAL}秒后重试")
            time.sleep(PING_INTERVAL)
    write_log(f"❌ 局域网连通性检查最终失败（重试{PING_RETRY}次后仍不可达）")
    return False

def backup_to_local_lan(source_folder, target_path):
    """增量备份：新增文件直接复制，修改文件（修改时间/大小变动）覆盖更新，未变动文件跳过"""
    try:
        # 步骤1：检查目标基础目录，不存在则创建（首次备份全量复制）
        if not os.path.exists(target_path):
            os.makedirs(target_path)
            write_log(f"首次备份：创建目标基础目录 {target_path}（将全量复制所有文件建立基础备份）")
        
        # 步骤2：遍历源文件夹所有文件/目录，保持目录结构一致
        for root, dirs, files in os.walk(source_folder):
            # 构建当前源目录对应的目标目录路径
            relative_path = os.path.relpath(root, source_folder)
            current_target_dir = os.path.join(target_path, relative_path)
            
            # 创建当前目标目录（不存在则创建，保证目录结构和源目录一致）
            if not os.path.exists(current_target_dir):
                os.makedirs(current_target_dir)
                write_log(f"创建目录：{current_target_dir}")
            
            # 步骤3：遍历当前目录下的所有文件，进行增量判断
            for file in files:
                local_file_path = os.path.join(root, file)    # 源文件完整路径
                target_file_path = os.path.join(current_target_dir, file)  # 目标文件完整路径
                
                # 情况A：目标文件不存在 → 新增文件，直接备份
                if not os.path.exists(target_file_path):
                    shutil.copy2(local_file_path, target_file_path)  # 保留文件元数据
                    write_log(f"✅ 新增文件：{local_file_path} -> {target_file_path}")
                
                # 情况B：目标文件存在 → 双重判断是否修改，修改则更新备份
                else:
                    # 获取源文件和目标文件的修改时间、文件大小
                    local_mtime = os.path.getmtime(local_file_path)
                    local_size = os.path.getsize(local_file_path)
                    target_mtime = os.path.getmtime(target_file_path)
                    target_size = os.path.getsize(target_file_path)
                    
                    # 双重判断：修改时间不同 或 文件大小不同 → 判定为已修改
                    if local_mtime != target_mtime or local_size != target_size:
                        shutil.copy2(local_file_path, target_file_path)  # 覆盖更新目标文件
                        write_log(f"✅ 更新文件（修改/大小变动）：{local_file_path} -> {target_file_path}")
                    # 两者均一致 → 未变动，跳过备份
                    else:
                        write_log(f"ℹ️  跳过未变动文件：{local_file_path}")
        
        write_log(f"✅ 增量备份完成：{source_folder} -> {target_path}（同步新增+修改文件，未变动文件跳过）")
        return True
    except Exception as e:
        write_log(f"❌ 局域网增量备份失败：{source_folder} - 错误：{str(e)}")
        return False

def backup_to_smb_server(source_folder):
    """备份到局域网SMB服务器（通过网线+IP连接）"""
    # 先检查网络连通性，不通则直接返回
    if not check_lan_connectivity(SMB_SERVER_IP):
        return False
    
    try:
        # 建立SMB连接（适配局域网网线场景）
        conn = SMBConnection(
            SMB_USERNAME,
            SMB_PASSWORD,
            "lan_backup_client",  # 客户端标识（可自定义）
            SMB_SERVER_IP,
            use_ntlm_v2=True,     # 局域网SMB常用NTLMv2认证
            is_direct_tcp=False   # 网线连接用139端口（非TCP直连）
        )
        
        # 连接服务器
        if not conn.connect(SMB_SERVER_IP, SMB_SERVER_PORT):
            write_log("❌ SMB服务器连接失败：用户名/密码错误或权限不足")
            return False
        
        # 遍历源文件夹，保持目录结构上传
        write_log(f"📤 开始上传文件夹：{source_folder}")
        for root, dirs, files in os.walk(source_folder):
            # 计算相对路径，构建远程目录
            relative_path = os.path.relpath(root, source_folder)
            remote_dir = relative_path.replace("\\", "/")  # SMB协议用/分隔路径
            
            # 创建远程目录（不存在则创建，已存在则忽略）
            try:
                conn.createDirectory(SMB_SHARE_NAME, remote_dir)
            except:
                pass
            
            # 逐个上传文件
            for file in files:
                local_file_path = os.path.join(root, file)
                remote_file_path = os.path.join(remote_dir, file).replace("\\", "/")
                
                # 读取本地文件并上传到服务器
                with open(local_file_path, "rb") as f:
                    conn.storeFile(SMB_SHARE_NAME, remote_file_path, f)
                
                write_log(f"✅ 上传成功：{local_file_path} -> smb://{SMB_SERVER_IP}/{SMB_SHARE_NAME}/{remote_file_path}")
        
        # 关闭连接
        conn.close()
        write_log(f"✅ 整个文件夹备份完成：{source_folder}")
        return True
    except Exception as e:
        write_log(f"❌ SMB服务器备份失败：{source_folder} - 错误：{str(e)}")
        return False

def main():
    """主执行逻辑（适配局域网网线连接场景）"""
    write_log("="*60)
    write_log("🚀 开始执行局域网自动备份任务（网线连接）")
    
    # 延迟15秒：等待系统加载网卡、网线连接建立
    write_log("⏳ 等待系统和网络加载完成...")
    time.sleep(15)
    
    # 校验源文件夹是否为空
    if not SOURCE_FOLDERS or SOURCE_FOLDERS == [""]:
        write_log("❌ 未配置要备份的源文件夹（请检查.env文件的SOURCE_FOLDERS）")
        return
    
    # 遍历所有要备份的文件夹
    for folder in SOURCE_FOLDERS:
        # 检查源文件夹是否存在
        if not os.path.isdir(folder):
            write_log(f"❌ 源文件夹不存在：{folder}，跳过该文件夹")
            continue
        
        # 根据备份类型执行对应逻辑
        if BACKUP_TYPE == "local_lan":
            backup_to_local_lan(folder, LOCAL_LAN_PATH)
        elif BACKUP_TYPE == "smb_server":
            backup_to_smb_server(folder)
        else:
            write_log(f"❌ 不支持的备份类型：{BACKUP_TYPE}（仅支持local_lan/smb_server）")
    
    write_log("🏁 局域网备份任务执行完毕")
    write_log("="*60 + "\n")

# 程序入口
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        write_log(f"💥 备份任务异常终止：{str(e)}")
        sys.exit(1)