import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { i18n, setupI18n } from "../src/i18n/index";

setupI18n("zh");

// jsdom 没有画布：getContext 一律返回 null（相当于没有 WebGL，地球模式按设计退回平面地图），也不再刷“未实现”的警告
if (typeof HTMLCanvasElement !== "undefined") {
  HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext;
}

// vitest 没有开全局 API，Testing Library 不会自己清理：每个测试之后卸载渲染出来的组件，语言回到中文
afterEach(async () => {
  cleanup();
  await i18n.changeLanguage("zh");
});
