// 全部项目跑完之后：删掉留给手机项目复用的测试身份（隔离服务的数据目录下次启动时也会清空）。
import { forgetIdentityA } from "./helpers";

export default function globalTeardown(): void {
  forgetIdentityA();
}
