from django.db import migrations, models
import django.db.models.deletion


def backfill_term(apps, schema_editor):
    """Every existing FeeStructure predates term-scoping -- attach each one
    to a Term 1 for its own academic_year, creating one if none exists yet
    (mirrors loadtest_provision.py's own Term-1-spanning-the-year fallback).
    """
    FeeStructure = apps.get_model("finance", "FeeStructure")
    Term = apps.get_model("academics", "Term")
    for structure in FeeStructure.objects.select_related("academic_year").all():
        term = Term.objects.filter(tenant=structure.tenant, academic_year=structure.academic_year, sequence=1).first()
        if term is None:
            term = Term.objects.create(
                tenant=structure.tenant, academic_year=structure.academic_year, sequence=1,
                name="Term 1", starts_on=structure.academic_year.starts_on, ends_on=structure.academic_year.ends_on,
            )
        structure.term = term
        structure.save(update_fields=["term"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    # Not atomic: the RunPython step INSERTs new academics_term rows, and
    # Postgres refuses a later ALTER TABLE in the same transaction while a
    # deferred FK-validation trigger from those inserts is still pending
    # ("cannot ALTER TABLE academics_term because it has pending trigger
    # events") -- letting each operation commit separately clears it.

    atomic = False

    dependencies = [
        ("academics", "0001_initial"),
        ("finance", "0008_mpesa_recovery_and_verification"),
    ]

    operations = [
        migrations.AddField(
            model_name="feestructure",
            name="term",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="fee_structures", to="academics.term"),
        ),
        migrations.RunPython(backfill_term, noop_reverse),
        migrations.AlterField(
            model_name="feestructure",
            name="term",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="fee_structures", to="academics.term"),
        ),
        migrations.RemoveConstraint(
            model_name="feestructure",
            name="unique_fee_structure_per_level_year",
        ),
        migrations.AddConstraint(
            model_name="feestructure",
            constraint=models.UniqueConstraint(fields=["tenant", "name", "academic_year", "academic_level", "term"], name="unique_fee_structure_per_level_year_term"),
        ),
    ]
