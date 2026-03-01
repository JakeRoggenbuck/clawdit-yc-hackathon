import { mutation, query } from "./_generated/server"
import { v } from "convex/values"

function normalizeSlug(entry) {
  if (typeof entry?.slug === "string" && entry.slug.trim()) {
    return entry.slug.trim()
  }
  return "unknown-skill"
}

export const list = query({
  args: {
    limit: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    const limit = Math.min(Math.max(args.limit ?? 5000, 1), 10000)
    const docs = await ctx.db.query("skillAudits").order("desc").take(limit)
    return docs.map((doc) => doc.entry)
  },
})

export const upsert = mutation({
  args: {
    entry: v.any(),
  },
  handler: async (ctx, args) => {
    const slug = normalizeSlug(args.entry)
    const existing = await ctx.db
      .query("skillAudits")
      .withIndex("by_slug", (q) => q.eq("slug", slug))
      .first()

    const updatedAt = Date.now()
    if (existing) {
      await ctx.db.patch(existing._id, { entry: args.entry, updatedAt })
      return existing._id
    }
    return ctx.db.insert("skillAudits", { slug, entry: args.entry, updatedAt })
  },
})

export const clearAll = mutation({
  args: {},
  handler: async (ctx) => {
    const docs = await ctx.db.query("skillAudits").collect()
    for (const doc of docs) {
      await ctx.db.delete(doc._id)
    }
    return { deleted: docs.length }
  },
})
