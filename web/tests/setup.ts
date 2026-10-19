import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// vitest 没有开全局 API，Testing Library 不会自己清理：每个测试之后卸载渲染出来的组件
afterEach(() => cleanup());
