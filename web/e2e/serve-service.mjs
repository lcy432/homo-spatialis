// Playwright 的 webServer：清空隔离服务的数据目录，起 E3C 的接口进程与 worker 进程（直接用仓库虚拟环境里的 Python，
// 不经过 uv，这样 Playwright 结束时能关掉整个进程树）。输出写进 runs/web-e2e/service-logs/（只在本机）。
// 只管自己起的这两个进程，不碰别的会话的服务。
// E5_SERVICE_REPO 指向另一份代码目录（例如只含已提交代码的干净工作树）时，服务从那里起：共享工作区里别的会话正在改、
// 还没导出结构的服务端代码不会混进界面的测试。
import { spawn } from "node:child_process";
import { createWriteStream, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..");
const serviceRepo = process.env.E5_SERVICE_REPO ? path.resolve(process.env.E5_SERVICE_REPO) : repo;
const evidence = path.join(repo, "runs", "web-e2e");
const dataDir = path.join(evidence, "service");
const logDir = path.join(evidence, "service-logs");
// 运行时配置：数据目录换成本仓库下的绝对路径（短路径；服务代码在别的工作树里也一样）。建链接的命令行读同一份（helpers.ts）。
const config = path.join(evidence, "service-e2e.runtime.yaml");
mkdirSync(evidence, { recursive: true });
writeFileSync(
  config,
  readFileSync(path.join(here, "service-e2e.yaml"), "utf8").replace(/^  data_dir: .*$/m, `  data_dir: ${dataDir.replaceAll("\\", "/")}`),
  "utf8",
);
const python = path.join(repo, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");

rmSync(dataDir, { recursive: true, force: true });
mkdirSync(logDir, { recursive: true });

const children = [];
for (const role of ["api", "worker"]) {
  const log = createWriteStream(path.join(logDir, `${role}.log`), { flags: "w" });
  const child = spawn(python, ["scripts/product_service.py", role, "--config", config], {
    cwd: serviceRepo,
    env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout.pipe(log);
  child.stderr.pipe(log);
  child.on("exit", (code) => {
    console.error(`[e2e service] ${role} exited with ${code}`);
    process.exit(code ?? 1);
  });
  children.push(child);
}

const stop = () => {
  for (const c of children) c.kill();
  process.exit(0);
};
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
console.log(`[e2e service] api and worker started on 127.0.0.1:8775 (code: ${serviceRepo === repo ? "this checkout" : "E5_SERVICE_REPO"})`);
