// 拍板动作：活公司请示 / 活公司验收 / 急停 / 场地发言。
// 旧工单执行与验收动作已归档，前端只保留历史查看入口。
import { postJSON } from './api'
import { toast } from './toast'

export type Refresh = () => void | Promise<void>

// 船主自然语言直控·确认卡三键（2026-07-11）：你说的管理指令→模型理解成一张卡→你点确认才执行/说清楚点纠正/取消作罢
export async function 确认管理(卡: string, refresh: Refresh) {
  try {
    const j = await postJSON<{ ok?: boolean; error?: string; 释义?: string }>('/管理_确认', { 卡 })
    if (!j.ok) throw new Error(j.error || '执行失败')
    toast('已执行：' + (j.释义 || '管理动作'), 'good')
  } catch (e) {
    toast('没执行：' + e, 'bad')
  }
  await refresh()
}

export async function 取消管理(卡: string, refresh: Refresh) {
  try {
    await postJSON<{ ok?: boolean }>('/管理_取消', { 卡 })
    toast('已取消', '')
  } catch (e) {
    toast('取消失败：' + e, 'bad')
  }
  await refresh()
}

export async function 说清楚点(卡: string, 补充: string, refresh: Refresh) {
  try {
    const j = await postJSON<{ ok?: boolean; error?: string; 说明?: string }>('/管理_重解', { 卡, 补充 })
    if (!j.ok) throw new Error(j.error || '重解失败')
    toast(j.说明 || '按你说的重新理解了，看新卡', 'good')
  } catch (e) {
    toast('重解失败：' + e, 'bad')
  }
  await refresh()
}

export async function 决策(id: string, v: '批' | '驳', reason: string, refresh: Refresh) {
  // M12：解析返回体，后端出错(ok:false)弹失败、别无脑弹绿色成功（原来出错也走通用200、船主以为成功）
  let ok = false
  try {
    const j = await postJSON<{ ok?: boolean; error?: string }>('/approval_decide', { id, v, reason })
    if (j.ok === false) throw new Error(j.error || '裁决失败')
    toast('请示已' + v, v === '批' ? 'good' : '')
    ok = true
  } catch (e) {
    toast('裁决失败：' + e, 'bad')
  }
  await refresh()
  return ok
}

// M13：船主手动调级——撤销/纠正自动升降职（后端 /职级_调级，可逆）
export async function 调级(人: string, 级: number, refresh: Refresh) {
  let ok = false
  try {
    const j = await postJSON<{ ok?: boolean; error?: string; 变动?: { 职称?: string } }>('/职级_调级', { 人, 级, 理由: '船主手调' })
    if (!j.ok) throw new Error(j.error || '调级失败')
    toast(`已把 ${人} 调为 ${j.变动?.职称 || ('级' + 级)}`, 'good')
    ok = true
  } catch (e) {
    toast('调级失败：' + e, 'bad')
  }
  await refresh()
  return ok
}

// 瞬时停止键：点一下终止当前全部在跑工作，收尾后后端自动恢复待命，不是开/关开关、不留长状态。
// 护栏丙②：活公司产出验收（批准=正式交付；打回=v1留痕，船主再发一句触发重做）
export async function 活公司验收(id: string, action: '批准' | '打回', 批注: string, refresh: Refresh) {
  let ok = false
  try {
    const j = await postJSON<{ ok?: boolean; error?: string }>('/活厅_验收', { id, action, 批注 })
    if (!j.ok) throw new Error(j.error || '验收失败')
    toast(action === '批准' ? '已批准交付' : '已打回', action === '批准' ? 'good' : '')
    ok = true
  } catch (e) {
    toast('验收失败：' + e, 'bad')
  }
  await refresh()
  return ok
}

export async function 急停(refresh: Refresh) {
  const j = await postJSON<{ reply?: string }>('/panic', {})
  toast(j.reply || '已停止当前全部工作', 'bad')
  await refresh()
}

export async function 会议发言(id: string, text: string, refresh: Refresh) {
  if (!text) return
  const j = await postJSON<{ reply?: string }>('/meeting_say', { id, text })
  toast(j.reply || '已写入会议', 'good')
  await refresh()
}
