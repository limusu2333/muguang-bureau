// 数据层 · 接现有办公室后端
// 生产：办公室.py 同源 serve；开发：vite proxy 把这些路径转发到 8787。
// 前端与办公室后端一起演进（2026-07-04 体检更新：旧'后端不改'注释已过时）。

// 活人工作单元：一次「唤醒本人」=一个活人在工作。父单元=开会时叫他来的那个人（PM）。
// 一个动作 = 一次工具调用：什么工具、什么参数、返回了什么结果，属于第几轮（同轮多个=并行批）。
export type 工作动作 = {
  动作id: string
  工具: string
  目标: string        // 参数：看什么文件/搜什么/议题
  结果: string        // 返回结果：找到哪些文件/会议纪要…
  轮: number          // 第几轮 reasoning，同轮多个=并行批
  状态: '进行' | '完成'
  成功: boolean
}
export type 活人单元 = {
  单元id: string
  父单元: string | null
  岗位: string
  名字: string
  场合: string        // 主事 | 参与
  会议id?: string
  会议主题?: string
  状态: '工作中' | '完成' | '在场'   // 在场=已进会议室、还没轮到发言（2026-07-09 船主：先进会议室、别一个个冒出来）
  思考: string        // 真实思考流（主模型怎么想；模型吐才有）
  动作: 工作动作[]    // 完整的工具调用轨迹（带参数+结果+轮次）
  发言: string        // 这个人最终说了什么
  起: number
  止?: number
}
export type HallEvent = {
  k: string // 话 | 立项 | 请示 | 决 | 验 | 会议 | 工作现场 | 分割
  t?: string
  who?: string
  role?: string
  text?: string
  副?: string
  room?: string
  event?: string
  桶?: string
  id?: string
  会议id?: string
  主题?: string
  状态?: string
  参会?: string[]
  参会岗位?: string[]   // 与参会同序的岗位名，开场预置"在场"占位单元用
  当前发言人?: string
  开始?: number
  结束?: number
  流式?: boolean // 前端临时态：打字机进行中
  目标?: string // 控速播放：后端已收到的完整文本(text=已显示;定时器把 text 匀速追向 目标,抹平后端爆发/卡顿)
  收尾?: boolean // 后端已发"发言结束"：text 追平 目标 后即定稿
  思考?: string // 思考期推理流(ThinkingBlockDelta)：等待期亮出"它在想什么",不黑箱
  工具?: string // 正在调用的工具名(ToolCallStart)：显示"正在查 xx 文件/搜什么"
}
export type SceneSummary = {
  日期: string
  摘要: string
  记录数: number
  最后时间?: string
  已整理?: boolean
}
export type Hall = { 事件: HallEvent[]; 历史日期?: string[]; 历史场次?: SceneSummary[] }

export type MeetingReplayLine = {
  时间?: string
  who?: string
  text?: string
  类型?: string
  会议id?: string
  主题?: string
  参会?: string[]
}

export type Member = {
  name: string
  名字?: string
  title?: string
  model?: string
  registered_model?: string
  abilities?: string
  modality?: string
  tier?: string
  actual_model?: string
  model_ok?: boolean
  评级?: number          // 1实习生 2正式员工 3部门主管 4副经理 5经理
  职称?: string
  是部门头?: boolean
  是经理?: boolean
  信任等级?: string      // 高 / 中 / 受限
  信誉分?: number        // 真实结果账按时间衰减后的当前分
  晋升分?: number        // 晋升曲线当前分（长跑：十几二十个成果才达标）
  晋升达标?: number      // 升下一级要攒到的分
  晋升试用?: boolean     // 达标后的 5 笔试用期
}

export type 健康探测 = {
  ok?: boolean
  状态?: string
  原因?: string
  毫秒?: number
  时间?: string
}
export type 健康任务明细 = {
  id?: string
  状态?: string
  类型?: string
  负责人?: string
  房间?: string
  更新时间?: string
  错误?: string
  内容?: string
}
export type 健康告警 = {
  类别?: '任务' | '系统' | '班子'
  级别?: '错误' | '警告'
  标题?: string
  详情?: string
  建议?: string
  目标?: string
  任务id?: string
  负责人?: string
  房间?: string
  更新时间?: string
  任务状态?: string
  负责人称谓?: string
  原始详情?: string
  指纹?: string
  表达来源?: '本地模型' | '规则' | string
  可选动作?: 健康告警动作[]
}
export type 健康告警动作 = {
  id: string
  标题?: string
  说明?: string
}
export type 健康告警处理 = {
  id: string
  时间?: string
  更新时间?: string
  动作?: string
  状态?: '已接收' | '正在交给项目经理' | '处理中' | '已完成' | '失败' | '稍后' | '状态不可读' | string
  补充?: string
  会话id?: string
  任务状态?: string
  结果?: string
  错误?: string
  告警?: 健康告警
}
export type 健康 = {
  班子?: Record<string, 健康探测>
  系统?: {
    ok?: boolean
    错误数?: number
    警告数?: number
    问题?: { 级别?: string; 区域?: string; 说明?: string }[]
  }
  任务?: {
    运行中?: number
    失败或中断?: number
    近7天失败或中断?: number
    近7天明细?: 健康任务明细[]
  }
  备份?: {
    最新?: string
    天数?: number | null
    可打开?: boolean
  }
  模型核验?: {
    存在?: boolean
    最旧天数?: number | null
  }
  统一搜索?: {
    status?: string
    checked_at?: string
    error?: string
  }
  告警?: 健康告警[]
  历史告警?: 健康告警[]
  告警表达?: {
    状态?: string
    可用?: boolean
    模型?: string
    加载秒?: number | null
    错误?: string
  }
  时间?: string
  error?: string
}

export type 汇总 = { 进行中?: number; 待确认?: number; 待验收?: number; 待办?: number }
export type Project = {
  id: string
  标题?: string
  活跃?: boolean
  待处理?: number
  汇总?: 汇总
  工单数?: number
}

export type 待确认项 = { 会议: string; 标题?: string; 角色?: string; name: string }
export type 轨迹步 = { 时间?: string; 层级?: string; 谁?: string; 动作?: string }
export type 裁决项 = { id: string; 岗位?: string; 内容?: string; 轨迹?: 轨迹步[]; 到你时间?: string }
export type 产物 = { 路径: string; 状态: string }
export type 经理报告 = { 理由?: string; 建议?: string; 依据?: string[]; 追加步数?: number }
export type 回炉项 = {
  name: string
  标题?: string
  角色?: string
  会议?: string
  步数?: number
  预算?: number | string
  产物?: 产物[]
  经理报告?: 经理报告
  请示?: { id?: string }
}
export type 验收项 = { name: string; 标题?: string; 角色?: string; 会议?: string }
export type 活公司验收项 = {
  id: string
  时间?: string
  岗位?: string
  任务?: string
  产物?: string
  文件?: string[] // 平台从工具轨迹收集的真实改动文件（不是模型自述）
  部门头?: string
  收敛经理?: string
  轨迹?: 轨迹步[]
  到你时间?: string
  状态?: string
  批注?: string
  来源?: string  // 交付＝走了完整交付链(必拍板)；大厅直答·可选验收＝直答里像成果的半强制留痕(可看可不看)
}
export type 已决项 = { 类?: string; id: string; 标题?: string; 岗位?: string; 裁决?: string; 理由?: string; 时间?: string }
// 船主自然语言直控·待确认卡（你说的管理指令→模型理解成这张卡，你点确认才执行）
export type 管理待确认项 = { id: string; 动作?: string; 原话?: string; 释义?: string; 影响?: string; 缺参?: string[]; 创建时间?: string }
export type 待办 = {
  待确认?: 待确认项[]
  待裁决?: 裁决项[]
  待验收?: 验收项[]
  待办?: 回炉项[]
  活公司待验收?: 活公司验收项[]
  已决?: 已决项[]
  管理待确认?: 管理待确认项[]
}

export type Board = {
  模式?: string
  急停?: boolean
  岗位?: string[]
  成员?: Member[]
  项目?: Project[]
  待办?: 待办
}

// ---- 项目室详情(/room?id=) ----
export type 模型简 = { title?: string; model?: string }
export type 产物详 = { 路径: string; 状态: string; 说明?: string }
export type 正式报告项 = {
  类别?: string
  时间?: string
  阶段?: string
  原因?: string
  判断?: string
  下一步?: string
}
export type 过程事件 = { id?: string; 类别?: string; 标题?: string; 说明?: string; 短时?: string }
export type 观察项 = { 摘要?: string; 原文?: string; 动作?: string; 目标?: string }
export type 过程 = {
  任务理解?: string
  执行要求?: string
  白名单?: string
  会议依据?: string
  预算依据?: string
  预计字数?: string
  预计token?: string
  产物?: 产物详[]
  正式报告?: 正式报告项[]
  事件?: 过程事件[]
  步骤?: string[]
  观察?: 观察项[]
}
export type 泳道 = {
  角色?: string
  name: string
  模型?: 模型简
  任务?: string
  列?: string
  步数?: number
  预算?: number | string
  请示中?: boolean
  控制状态?: string
  过程?: 过程
  裁决?: string[]
  日志?: string
}
export type 会议事件 = {
  id?: string
  类型?: string
  发言人?: string
  目标?: string
  关联工单?: string
  需回执?: boolean
  短时?: string
  内容?: string
}
export type 会议协同 = {
  id: string
  标题?: string
  类型?: string
  主席?: string
  状态?: string
  参会?: string[]
  事件?: 会议事件[]
}
export type 室记忆 = { who?: string; 时间?: string; text?: string }
export type 室请示 = {
  id: string
  岗位?: string
  桶?: string
  内容?: string
  建议?: string
  经理理由?: string
  经理裁决?: string
  船主理由?: string
  状态?: string
}
export type RoomDetail = {
  id: string
  标题?: string
  时间?: string
  需求?: string
  项目记忆?: 室记忆[]
  项目会?: string
  覆盖说明?: string
  泳道?: 泳道[]
  会议协同?: 会议协同[]
  请示?: 室请示[]
}

export async function getJSON<T = unknown>(url: string): Promise<T> {
  // 挂死的请求10秒斩断——LINK灯才有机会说真话（SIGSTOP级的假活连接，fetch默认会无限等）
  const r = await fetch(url, { signal: AbortSignal.timeout(10000) })
  if (!r.ok) throw new Error(`${url} ${r.status}`)
  return r.json() as Promise<T>
}

export async function postJSON<T = unknown>(url: string, body?: unknown, timeoutMs = 300000): Promise<T> {
  const r = await fetch(url, {
    signal: AbortSignal.timeout(timeoutMs),
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
  if (!r.ok) throw new Error(`${url} ${r.status}`)
  return r.json().catch(() => ({}) as T)
}
