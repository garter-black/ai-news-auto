"use client";

import dynamic from "next/dynamic";

/**
 * スマホの画面下の PR 帯（MobilePrBar）を、ページの表示後に読み込む。
 * 帯は読み進めてから出る固定表示なので、最初の表示（LCP）に要らない JS を後回しにする。画面の位置はずれない（fixed）
 */
const MobilePrBar = dynamic(() => import("./MobilePrBar"), { ssr: false });

export default MobilePrBar;
