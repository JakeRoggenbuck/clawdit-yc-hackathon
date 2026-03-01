import { defineSchema, defineTable } from "convex/server"
import { v } from "convex/values"

export default defineSchema({
  skillAudits: defineTable({
    slug: v.string(),
    entry: v.any(),
    updatedAt: v.number(),
  }).index("by_slug", ["slug"]),
})
