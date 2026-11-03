"""Удалённое обновление Aegra: git pull, restart и health check."""

import os
import time

import paramiko


def main():
    host = os.environ["DEPLOY_HOST"]
    user = os.environ["DEPLOY_USER"]
    password = os.environ["DEPLOY_PASSWORD"]
    git_url = os.environ["DEPLOY_GIT_URL"]
    remote_path = os.environ.get("DEPLOY_REMOTE_PATH", "/opt/logicore_agent")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password, timeout=20)

    def run(command, label="", timeout=180):
        print(f"\n=== {label or command} ===", flush=True)
        _, out, err = client.exec_command(command, timeout=timeout)
        stdout = out.read().decode("utf-8", "replace")
        stderr = err.read().decode("utf-8", "replace")
        if stdout:
            print(stdout)
        if stderr:
            print("STDERR:\n" + stderr)
        code = out.channel.recv_exit_status()
        print(f"rc={code}", flush=True)
        return code, stdout, stderr

    run(f"cd {remote_path} && git pull {git_url} main", "git pull", timeout=180)
    _, stdout, stderr = run(
        f"cd {remote_path} && docker compose restart aegra",
        "restart aegra",
        timeout=300,
    )
    print("RESTART REAL:", ("Restarting" in stdout + stderr) and ("Started" in stdout + stderr))

    healthy = False
    for index in range(40):
        time.sleep(5)
        code, stdout, _ = run("curl -sf http://localhost:2026/health", "health", timeout=15)
        if code == 0 and '"status":"healthy"' in stdout:
            print(f"HEALTHY после ~{index * 5}с")
            healthy = True
            break
    print("HEALTH OK" if healthy else "NOT HEALTHY")
    client.close()


if __name__ == "__main__":
    main()
