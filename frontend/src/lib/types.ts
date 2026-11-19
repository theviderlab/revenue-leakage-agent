// Aliases over the generated API types (api-types.ts); nothing here is written by hand.
import type { components } from './api-types'

type S = components['schemas']

export type ApiEvent = S['TurnResponse']['events'][number]
export type EventOf<T extends ApiEvent['type']> = Extract<ApiEvent, { type: T }>
export type TurnResponse = S['TurnResponse']
export type CreateThreadResponse = S['CreateThreadResponse']
export type ThreadState = S['ThreadState']
export type PlanFindings = S['PlanFindings']
export type FindingsPayload = S['FindingsPayload']
export type Period = S['Period']
export type Proposal = S['Proposal']
export type PendingAction = S['PendingAction']
export type PendingApproval = S['PendingApproval']
export type AuditAction = S['AuditAction']
export type Plan = S['Plan']
export type SettingInfo = S['SettingInfo']
export type SettingsResponse = S['SettingsResponse']
export type SettingsUpdate = S['SettingsUpdate']
export type Decision = S['DecisionRequest']['decision']
export type ToolStatus = EventOf<'tool_result'>['status']
