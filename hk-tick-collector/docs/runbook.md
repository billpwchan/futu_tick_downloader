# 部署与运维 Runbook

## 1) 安装依赖

```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv sqlite3 telnet netcat-openbsd
```

建议设置系统时区：

```bash
sudo timedatectl set-timezone Asia/Hong_Kong
```

## 2) 安装官方 OpenD 包

1. 从富途官方入口下载 Ubuntu/CentOS 发行包。
2. 解压到 `/opt/futu-opend`：

```bash
sudo mkdir -p /opt/futu-opend
sudo tar -xzf FutuOpenD*.tar.gz -C /opt/futu-opend
```

建议创建独立用户运行 OpenD：

```bash
sudo useradd --system --home /opt/futu-opend --shell /usr/sbin/nologin futu
sudo chown -R futu:futu /opt/futu-opend
```

## 3) 配置 OpenD.xml

要求：监听 `127.0.0.1`，API 端口 `11111`，启用 telnet 端口 `22222`。
登录凭证优先使用 `login_pwd_md5`，且配置文件权限需为 `600`。

示例片段（仅供参考，字段名以官方模板为准）：

```xml
<OpenD>
  <ip>127.0.0.1</ip>
  <port>11111</port>
  <telnet_ip>127.0.0.1</telnet_ip>
  <telnet_port>22222</telnet_port>
  <login_user>your_account</login_user>
  <login_pwd_md5>your_md5_password</login_pwd_md5>
</OpenD>
```

设置权限：

```bash
sudo chmod 600 /opt/futu-opend/OpenD.xml
```

## 4) systemd 启动 OpenD

```bash
sudo cp ops/futu-opend.service /etc/systemd/system/futu-opend.service
sudo systemctl daemon-reload
sudo systemctl enable --now futu-opend.service
```

## 5) 配置采集器环境变量

```bash
sudo cp .env.example /etc/hk-tick-collector.env
sudo nano /etc/hk-tick-collector.env
```

至少设置：

- `FUTU_SYMBOLS=HK.00700,HK.00981`
- `DATA_ROOT=/data/sqlite/HK`

## 6) 安装并启动采集器

```bash
sudo ops/install_collector.sh
```

## 7) 输入验证码（telnet 本机端口）

```bash
telnet 127.0.0.1 22222
```

在 telnet 控制台输入：

```
req_phone_verify_code
input_phone_verify_code -code=123456
```

## 8) 验证

```bash
sudo ops/verify.sh
```

输出包含：端口监听、订阅权限、当日数据库写入情况。
