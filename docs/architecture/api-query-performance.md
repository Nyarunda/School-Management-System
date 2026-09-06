# API Query Performance Standard

Every read endpoint has an intentional query shape. Querysets, not serializers, own database access strategy.

- Apply tenant filtering in the selector.
- Use `select_related` for required foreign keys.
- Use targeted `prefetch_related` for reverse or many relationships.
- Fetch only fields required by the response when projection is safe.
- Filter, order, and paginate in the database.
- Keep list endpoints cheaper than detail endpoints.
- Include tenant identity in cache keys.
- Add query-count regression tests for critical endpoints and assert bounded growth with representative data.
- Use `QuerySet.explain()` against PostgreSQL for expensive or high-volume paths.

A serializer must not hide repeated relationship access, unbounded properties, or per-row queries. When a nested response needs more data, update the selector and the query regression test together.
