"""Category → verse pack seed. KJV (public domain).

Seed texts were entered by hand and should be proofread against a printed KJV
before production use. References follow the spec's FR-2.3 packs (subset for
the vertical slice; extend with the remaining packs in phase 4).
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m

CATEGORIES = {
    "healing": "Healing / Illness",
    "grief": "Grief / Loss",
    "anxiety": "Anxiety / Fear",
    "provision": "Finances / Provision",
    "salvation": "Salvation of a Loved One",
    "family": "Marriage / Family",
    "guidance": "Guidance / Decisions",
    "travel": "Travel / Protection",
    "strength": "Strength / Perseverance",
    "thanksgiving": "Thanksgiving / Praise",
    "general": "General / Other",
}

VERSES: dict[str, list[tuple[str, str]]] = {
    "healing": [
        ("James 5:15", "And the prayer of faith shall save the sick, and the Lord shall raise him up; and if he have committed sins, they shall be forgiven him."),
        ("Jeremiah 17:14", "Heal me, O LORD, and I shall be healed; save me, and I shall be saved: for thou art my praise."),
        ("Psalm 103:2-3", "Bless the LORD, O my soul, and forget not all his benefits: Who forgiveth all thine iniquities; who healeth all thy diseases;"),
    ],
    "grief": [
        ("Psalm 34:18", "The LORD is nigh unto them that are of a broken heart; and saveth such as be of a contrite spirit."),
        ("Matthew 5:4", "Blessed are they that mourn: for they shall be comforted."),
        ("Psalm 147:3", "He healeth the broken in heart, and bindeth up their wounds."),
    ],
    "anxiety": [
        ("Philippians 4:6-7", "Be careful for nothing; but in every thing by prayer and supplication with thanksgiving let your requests be made known unto God. And the peace of God, which passeth all understanding, shall keep your hearts and minds through Christ Jesus."),
        ("1 Peter 5:7", "Casting all your care upon him; for he careth for you."),
        ("Isaiah 41:10", "Fear thou not; for I am with thee: be not dismayed; for I am thy God: I will strengthen thee; yea, I will help thee; yea, I will uphold thee with the right hand of my righteousness."),
    ],
    "provision": [
        ("Philippians 4:19", "But my God shall supply all your need according to his riches in glory by Christ Jesus."),
        ("Matthew 6:33", "But seek ye first the kingdom of God, and his righteousness; and all these things shall be added unto you."),
        ("Psalm 37:25", "I have been young, and now am old; yet have I not seen the righteous forsaken, nor his seed begging bread."),
    ],
    "salvation": [
        ("2 Peter 3:9", "The Lord is not slack concerning his promise, as some men count slackness; but is longsuffering to us-ward, not willing that any should perish, but that all should come to repentance."),
        ("Acts 16:31", "And they said, Believe on the Lord Jesus Christ, and thou shalt be saved, and thy house."),
        ("Romans 10:9", "That if thou shalt confess with thy mouth the Lord Jesus, and shalt believe in thine heart that God hath raised him from the dead, thou shalt be saved."),
    ],
    "family": [
        ("Joshua 24:15", "...but as for me and my house, we will serve the LORD."),
        ("Ecclesiastes 4:12", "And if one prevail against him, two shall withstand him; and a threefold cord is not quickly broken."),
        ("Colossians 3:13", "Forbearing one another, and forgiving one another, if any man have a quarrel against any: even as Christ forgave you, so also do ye."),
    ],
    "guidance": [
        ("Proverbs 3:5-6", "Trust in the LORD with all thine heart; and lean not unto thine own understanding. In all thy ways acknowledge him, and he shall direct thy paths."),
        ("James 1:5", "If any of you lack wisdom, let him ask of God, that giveth to all men liberally, and upbraideth not; and it shall be given him."),
        ("Psalm 119:105", "Thy word is a lamp unto my feet, and a light unto my path."),
    ],
    "travel": [
        ("Psalm 121:7-8", "The LORD shall preserve thee from all evil: he shall preserve thy soul. The LORD shall preserve thy going out and thy coming in from this time forth, and even for evermore."),
        ("Psalm 91:11", "For he shall give his angels charge over thee, to keep thee in all thy ways."),
        ("Isaiah 43:2", "When thou passest through the waters, I will be with thee; and through the rivers, they shall not overflow thee..."),
    ],
    "strength": [
        ("Isaiah 40:31", "But they that wait upon the LORD shall renew their strength; they shall mount up with wings as eagles; they shall run, and not be weary; and they shall walk, and not faint."),
        ("Philippians 4:13", "I can do all things through Christ which strengtheneth me."),
        ("Joshua 1:9", "Have not I commanded thee? Be strong and of a good courage; be not afraid, neither be thou dismayed: for the LORD thy God is with thee whithersoever thou goest."),
    ],
    "thanksgiving": [
        ("Psalm 100:4", "Enter into his gates with thanksgiving, and into his courts with praise: be thankful unto him, and bless his name."),
        ("1 Thessalonians 5:16-18", "Rejoice evermore. Pray without ceasing. In every thing give thanks: for this is the will of God in Christ Jesus concerning you."),
        ("Psalm 107:1", "O give thanks unto the LORD, for he is good: for his mercy endureth for ever."),
    ],
    "general": [
        ("Romans 8:28", "And we know that all things work together for good to them that love God, to them who are the called according to his purpose."),
        ("Psalm 46:1", "God is our refuge and strength, a very present help in trouble."),
        ("Matthew 7:7", "Ask, and it shall be given you; seek, and ye shall find; knock, and it shall be opened unto you:"),
    ],
}


async def seed_verses(db: AsyncSession) -> None:
    existing = (await db.execute(select(m.Verse.id).limit(1))).first()
    if existing:
        return
    for slug, pack in VERSES.items():
        for i, (ref, text) in enumerate(pack):
            db.add(m.Verse(category_slug=slug, reference=ref, text=text, position=i))
    await db.commit()
