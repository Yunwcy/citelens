import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api";
import { STRINGS, detectLang, type Lang } from "./i18n";
import { Chat, type Turn } from "./components/Chat";
import { DocumentPanel, type Job } from "./components/DocumentPanel";
import { SourcePanel } from "./components/SourcePanel";

const LANG_KEY = "citegrain.lang";
const HISTORY_KEY = "citegrain.history";

export default function App() {
  const [lang, setLang] = useState<Lang>(
    () => (localStorage.getItem(LANG_KEY) as Lang) ?? detectLang(),
  );
  const t = STRINGS[lang];
  const [documents, setDocuments] = useState<api.DocSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  // 串流回呼在非同步中執行，必須讀取當下的選取，不能捕捉舊值
  const activeIdRef = useRef<string | null>(null);
  // 進行中的索引任務獨立於選取狀態：使用者在處理期間切換文件時，
  // 任務仍要繼續追蹤，卡片也不該消失。
  const [job, setJob] = useState<Job | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quick, setQuick] = useState<string[]>([]);
  // 對話依文件保存。切換文件時清空會讓誤觸側欄＝刪除紀錄，
  // 而使用者常在讀答案時順手點到另一份文件。
  // 對話寫進 sessionStorage：重新整理（Demo 時很容易手滑）不該讓紀錄消失。
  // 用 session 而非 local：對話屬於這次工作階段，關掉分頁就該結束，
  // 否則舊紀錄會無限累積。
  const [history, setHistory] = useState<Record<string, Turn[]>>(() => {
    try {
      return JSON.parse(sessionStorage.getItem(HISTORY_KEY) ?? "{}");
    } catch {
      return {};
    }
  });

  useEffect(() => {
    try {
      sessionStorage.setItem(HISTORY_KEY, JSON.stringify(history));
    } catch {
      // 超出配額時放棄保存，但不影響使用 —— 對話仍在記憶體裡
    }
  }, [history]);
  const turns = activeId ? history[activeId] ?? [] : [];
  const setTurns = useCallback((fn: Turn[] | ((prev: Turn[]) => Turn[])) => {
    setHistory((all) => {
      const id = activeIdRef.current;
      if (!id) return all;
      const prev = all[id] ?? [];
      return { ...all, [id]: typeof fn === "function" ? fn(prev) : fn };
    });
  }, []);
  const [busy, setBusy] = useState(false);
  // busy 是 React state，更新是非同步的 —— 同一個 tick 內連按三次，
  // 三次都會讀到 busy=false 而全部送出。實測結果是三個提問、一個答案，
  // 因為三條串流都寫進了「最後一個」turn。守衛必須是同步的。
  const busyRef = useRef(false);

  useEffect(() => { localStorage.setItem(LANG_KEY, lang); }, [lang]);
  useEffect(() => { activeIdRef.current = activeId; }, [activeId]);

  const refresh = useCallback(async () => {
    const docs = await api.listDocuments();
    setDocuments(docs);
    return docs;
  }, []);

  useEffect(() => {
    refresh().then((docs) => {
      if (docs.length && !activeId) select(docs[0].doc_id);
    }).catch((e) => setError(String(e.message ?? e)));
  }, []);

  // 切換語言只重新取快速提問，不重設對話 ——
  // select() 會清空 turns，切語言時等於把使用者的對話紀錄刪掉。
  useEffect(() => {
    if (!activeId) return;
    api.getDocument(activeId, lang).then((d) => setQuick(d.quick_questions)).catch(() => {});
  }, [lang, activeId]);

  async function select(id: string) {
    setActiveId(id);
    activeIdRef.current = id;
    setReady(false);
    try {
      const doc = await api.getDocument(id, lang);
      setQuick(doc.quick_questions);
      setReady(true);
    } catch (e: any) {
      setError(describe(e));
    }
  }

  function describe(e: any): string {
    if (e instanceof api.HttpError) {
      if (e.detail) return e.detail;
      if (e.status === 413) return t.errorTooLarge;
      if (e.status === 502) return t.errorBackend;
      if (e.status === 504) return t.errorTimeout;
      return t.errorGeneric(e.status);
    }
    return e?.message ?? String(e);
  }

  async function handleDelete(id: string) {
    setError(null);
    try {
      await api.deleteDocument(id);
    } catch (e: any) {
      setError(describe(e));
      return;
    }
    // 連同該文件的對話紀錄一起清掉：留著會在下次上傳同一份文件時
    // （doc_id 由內容雜湊而來，會是同一個）憑空冒出舊對話
    setHistory((all) => {
      const { [id]: _gone, ...rest } = all;
      return rest;
    });
    const docs = await refresh();
    if (activeId === id) {
      if (docs.length) {
        select(docs[0].doc_id);
      } else {
        setActiveId(null);
        activeIdRef.current = null;
        setReady(false);
        setQuick([]);
      }
    }
  }

  const handleUpload = (file: File) => start(() => api.upload(file), file.name);
  const handleUploadUrl = (url: string) =>
    start(() => api.uploadFromUrl(url), lang === "zh" ? "由網址匯入" : "From link");

  async function start(
    begin: () => Promise<{ job_id: string; doc_id: string }>,
    label: string,
  ) {
    setError(null);
    try {
      const { job_id, doc_id } = await begin();
      setJob({ docId: doc_id, filename: label, stage: "queued" });

      for await (const ev of api.jobEvents(job_id)) {
        setJob((j) =>
          j && j.docId === doc_id
            ? { ...j, stage: ev.stage, pages: ev.pages, chunks: ev.chunks, tables: ev.tables }
            : j,
        );
        if (ev.error) { setError(ev.error); break; }
        if (ev.stage === "ready") {
          await refresh();
          setJob(null);
          // 只有在使用者沒有切走時才自動開啟，避免打斷正在進行的對話
          setActiveId((cur) => {
            if (cur === null || cur === doc_id) { void select(doc_id); return doc_id; }
            return cur;
          });
          break;
        }
      }
    } catch (e: any) {
      setJob((j) => (j ? { ...j, stage: "failed" } : j));
      setError(describe(e));
    }
  }

  async function ask(question: string) {
    if (!activeId || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setTurns((t) => [...t, {
      question, answer: "", sources: [], debug: null,
      stage: null, progress: null, declined: false, truncated: false, broken: false,
    }]);

    const patch = (fn: (t: Turn) => Turn) =>
      setTurns((all) => all.map((t, i) => (i === all.length - 1 ? fn(t) : t)));

    try {
      for await (const ev of api.askStream(activeId, question)) {
        if (ev.type === "stage")
          patch((t) => ({
            ...t,
            stage: ev.stage,
            // 摘要階段帶進度；一般問答的階段沒有 total，維持既有顯示
            progress: ev.stage.startsWith("summary_") && ev.total
              ? { phase: ev.stage.slice("summary_".length),
                  done: ev.done ?? 0, total: ev.total }
              : t.progress,
          }));
        else if (ev.type === "token") patch((t) => ({ ...t, answer: t.answer + ev.text }));
        else if (ev.type === "done")
          patch((t) => ({
            ...t, sources: ev.sources, debug: ev.debug,
            stage: null, progress: null, declined: ev.debug.declined === true,
            truncated: ev.debug.answer_truncated === true,
            broken: !!ev.debug.stream_error,
          }));
      }
    } catch (e: any) {
      patch((turn) => ({
        // 有部分文字時也要標明中斷 —— 否則半截答案看起來像是「比較短的回答」
        ...turn, answer: turn.answer || t.answerFailed(describe(e)),
        stage: null, progress: null, broken: true,
      }));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  /** 標記取材自 PEGA AI 識別的長條＋弧形結構，重新繪製而非沿用原圖。 */
  const Mark = () => (
    <svg width="22" height="22" viewBox="0 0 1254 1254" aria-hidden="true" className="shrink-0">
      <path d="M 706 562 L 680 589 L 678 593 L 675 596 L 673 600 L 670 603 L 662 615 L 657 626 L 655 628 L 651 636 L 651 638 L 647 645 L 647 647 L 642 660 L 642 663 L 641 664 L 641 667 L 640 668 L 640 671 L 639 672 L 639 675 L 637 680 L 637 684 L 636 685 L 635 700 L 634 701 L 634 737 L 635 738 L 635 745 L 636 746 L 636 752 L 637 753 L 638 762 L 640 766 L 642 777 L 643 778 L 646 789 L 662 821 L 679 845 L 690 856 L 690 857 L 706 872 L 707 872 L 719 882 L 728 887 L 733 891 L 748 899 L 750 899 L 764 906 L 769 907 L 774 910 L 777 910 L 778 911 L 784 912 L 792 915 L 802 916 L 803 917 L 808 917 L 809 918 L 816 918 L 817 919 L 850 920 L 851 919 L 869 918 L 870 917 L 875 917 L 876 916 L 881 916 L 886 914 L 890 914 L 891 913 L 894 913 L 895 912 L 909 908 L 926 900 L 928 900 L 938 895 L 940 893 L 949 889 L 951 887 L 1046 982 L 1047 982 L 1049 985 L 1050 985 L 1075 1011 L 1076 1011 L 1092 1027 L 1095 1028 L 1097 1030 L 1099 1030 L 1102 1032 L 1105 1032 L 1106 1033 L 1119 1033 L 1120 1032 L 1123 1032 L 1133 1027 L 1137 1024 L 1142 1018 L 1146 1010 L 1146 1007 L 1147 1006 L 1147 993 L 1146 992 L 1145 987 L 1140 978 L 1003 840 L 1004 839 L 1004 825 L 1003 824 L 1002 819 L 996 809 L 988 802 L 979 797 L 973 796 L 972 795 L 955 795 L 954 796 L 951 796 L 936 804 L 922 818 L 918 820 L 910 827 L 903 830 L 901 832 L 891 837 L 886 838 L 883 840 L 881 840 L 874 843 L 867 844 L 866 845 L 855 846 L 854 847 L 837 848 L 836 847 L 823 847 L 822 846 L 817 846 L 816 845 L 807 844 L 806 843 L 798 841 L 795 839 L 787 837 L 770 828 L 756 818 L 741 803 L 729 787 L 722 774 L 722 772 L 719 767 L 719 765 L 717 762 L 717 760 L 714 753 L 713 745 L 712 744 L 712 739 L 711 738 L 711 730 L 710 729 L 710 710 L 711 709 L 711 702 L 712 701 L 712 696 L 714 691 L 714 687 L 715 686 L 719 672 L 722 667 L 722 665 L 726 657 L 739 637 L 757 618 L 758 618 L 767 610 L 779 602 L 796 593 L 798 593 L 799 592 L 804 591 L 807 589 L 813 588 L 817 586 L 825 585 L 826 584 L 832 584 L 833 583 L 837 583 L 838 582 L 863 582 L 864 583 L 871 583 L 872 584 L 876 584 L 877 585 L 884 586 L 885 587 L 896 590 L 911 597 L 927 608 L 946 627 L 948 631 L 952 635 L 957 638 L 959 638 L 963 640 L 977 640 L 978 641 L 995 641 L 996 640 L 1001 640 L 1002 639 L 1005 639 L 1012 635 L 1018 628 L 1022 619 L 1022 605 L 1019 598 L 1012 590 L 1010 586 L 1000 574 L 1000 573 L 994 567 L 994 566 L 984 557 L 983 557 L 975 549 L 971 547 L 963 540 L 957 537 L 952 533 L 932 523 L 930 523 L 927 521 L 922 520 L 919 518 L 917 518 L 910 515 L 907 515 L 906 514 L 903 514 L 902 513 L 899 513 L 894 511 L 884 510 L 883 509 L 877 509 L 876 508 L 868 508 L 867 507 L 835 507 L 834 508 L 826 508 L 825 509 L 819 509 L 818 510 L 808 511 L 807 512 L 804 512 L 799 514 L 795 514 L 794 515 L 791 515 L 790 516 L 779 519 L 776 521 L 771 522 L 761 527 L 759 527 L 755 530 L 751 531 L 747 534 L 742 536 L 737 540 L 726 546 L 714 556 L 713 556 L 707 562 Z M 918 178 L 918 326 L 919 327 L 919 333 L 920 334 L 920 337 L 921 338 L 922 343 L 927 353 L 930 356 L 932 360 L 941 369 L 942 369 L 950 376 L 961 381 L 963 381 L 967 383 L 970 383 L 971 384 L 981 384 L 982 385 L 1108 385 L 1109 384 L 1121 384 L 1121 383 L 1008 269 L 1008 268 L 961 221 L 961 220 L 919 178 Z M 546 152 L 531 159 L 529 161 L 520 166 L 501 183 L 488 201 L 482 213 L 482 215 L 480 218 L 480 220 L 477 227 L 477 230 L 475 235 L 475 239 L 474 240 L 474 247 L 473 248 L 473 397 L 472 398 L 472 405 L 471 406 L 471 409 L 470 410 L 469 415 L 462 428 L 448 441 L 438 446 L 436 446 L 432 448 L 427 448 L 426 449 L 275 449 L 274 450 L 267 451 L 258 456 L 246 469 L 243 475 L 242 481 L 241 482 L 241 497 L 242 498 L 242 501 L 246 511 L 257 523 L 263 527 L 265 527 L 269 529 L 272 529 L 273 530 L 277 530 L 278 531 L 526 531 L 527 532 L 530 532 L 540 537 L 547 544 L 551 550 L 551 552 L 553 556 L 553 570 L 552 571 L 552 574 L 547 582 L 538 590 L 528 594 L 152 594 L 151 595 L 145 595 L 144 596 L 139 597 L 132 601 L 121 612 L 116 622 L 116 625 L 114 630 L 114 641 L 115 642 L 115 646 L 116 647 L 117 652 L 125 664 L 132 670 L 142 675 L 150 676 L 151 677 L 500 677 L 501 678 L 507 679 L 513 682 L 523 692 L 526 698 L 527 704 L 528 705 L 528 718 L 527 719 L 526 724 L 522 731 L 518 735 L 517 735 L 513 739 L 501 744 L 259 744 L 258 745 L 250 746 L 242 750 L 238 753 L 229 763 L 226 769 L 225 775 L 224 776 L 224 789 L 225 790 L 226 796 L 231 805 L 239 813 L 245 817 L 251 818 L 252 819 L 256 819 L 257 820 L 488 820 L 498 824 L 506 832 L 510 840 L 510 846 L 511 847 L 510 849 L 510 855 L 509 856 L 509 858 L 506 863 L 499 870 L 494 873 L 492 873 L 488 875 L 366 875 L 365 876 L 358 877 L 348 883 L 342 889 L 338 896 L 337 902 L 336 903 L 336 916 L 337 917 L 337 920 L 339 925 L 345 933 L 351 938 L 357 941 L 359 941 L 363 943 L 371 943 L 372 944 L 377 944 L 378 943 L 436 943 L 437 944 L 439 944 L 440 943 L 448 943 L 449 944 L 456 944 L 457 945 L 460 945 L 464 947 L 470 953 L 472 956 L 476 970 L 485 987 L 502 1006 L 516 1016 L 534 1025 L 536 1025 L 540 1027 L 543 1027 L 547 1029 L 554 1030 L 555 1031 L 572 1032 L 573 1033 L 843 1033 L 844 1032 L 854 1032 L 855 1031 L 867 1030 L 868 1029 L 879 1027 L 880 1026 L 891 1023 L 914 1012 L 919 1008 L 925 1005 L 940 992 L 941 992 L 949 984 L 949 983 L 957 975 L 963 967 L 963 964 L 964 963 L 963 962 L 963 959 L 961 955 L 958 952 L 954 950 L 946 950 L 931 957 L 929 957 L 910 964 L 907 964 L 906 965 L 903 965 L 902 966 L 899 966 L 894 968 L 880 970 L 879 971 L 865 972 L 864 973 L 853 973 L 852 974 L 824 974 L 823 973 L 811 973 L 810 972 L 803 972 L 802 971 L 790 970 L 785 968 L 781 968 L 780 967 L 776 967 L 775 966 L 764 964 L 763 963 L 740 956 L 731 951 L 729 951 L 709 941 L 704 937 L 701 936 L 699 934 L 690 929 L 670 914 L 654 898 L 653 898 L 637 880 L 637 879 L 626 866 L 620 857 L 619 854 L 615 849 L 614 846 L 612 844 L 597 812 L 597 810 L 596 809 L 595 804 L 593 801 L 592 795 L 588 785 L 587 777 L 586 776 L 585 768 L 584 767 L 583 755 L 582 754 L 581 723 L 580 722 L 580 717 L 581 716 L 581 700 L 582 699 L 583 682 L 584 681 L 584 676 L 585 675 L 587 661 L 589 657 L 591 646 L 594 640 L 594 637 L 595 636 L 596 631 L 598 628 L 598 626 L 602 619 L 602 617 L 616 588 L 618 586 L 620 581 L 628 569 L 638 557 L 644 548 L 650 542 L 650 541 L 676 515 L 686 508 L 695 500 L 704 495 L 709 491 L 712 490 L 714 488 L 717 487 L 719 485 L 722 484 L 724 482 L 742 473 L 744 473 L 751 469 L 756 468 L 759 466 L 761 466 L 771 462 L 774 462 L 778 460 L 781 460 L 788 457 L 802 455 L 806 453 L 818 452 L 819 451 L 827 451 L 828 450 L 876 450 L 877 451 L 892 452 L 893 453 L 897 453 L 902 455 L 911 456 L 919 459 L 926 460 L 932 463 L 946 467 L 979 483 L 1007 502 L 1019 513 L 1020 513 L 1031 524 L 1031 525 L 1039 532 L 1047 543 L 1052 548 L 1063 565 L 1068 575 L 1068 577 L 1070 581 L 1070 585 L 1071 586 L 1071 600 L 1070 601 L 1070 605 L 1064 619 L 1059 626 L 1053 632 L 1040 641 L 1040 647 L 1041 648 L 1041 651 L 1045 661 L 1046 669 L 1047 670 L 1048 678 L 1049 679 L 1049 685 L 1050 686 L 1050 694 L 1051 695 L 1051 734 L 1050 735 L 1050 743 L 1049 744 L 1049 749 L 1048 750 L 1047 760 L 1046 761 L 1044 772 L 1043 773 L 1039 787 L 1037 790 L 1036 795 L 1031 805 L 1031 809 L 1035 813 L 1035 814 L 1036 814 L 1073 851 L 1074 851 L 1094 872 L 1098 875 L 1106 878 L 1115 878 L 1116 877 L 1119 877 L 1128 872 L 1138 861 L 1145 848 L 1145 846 L 1147 843 L 1149 832 L 1150 831 L 1150 790 L 1151 789 L 1151 780 L 1150 779 L 1150 777 L 1151 776 L 1151 763 L 1150 762 L 1150 758 L 1151 757 L 1151 731 L 1150 730 L 1150 721 L 1151 720 L 1151 689 L 1150 688 L 1150 683 L 1151 682 L 1151 649 L 1150 648 L 1150 642 L 1151 641 L 1151 620 L 1150 619 L 1150 613 L 1151 612 L 1151 601 L 1150 600 L 1151 598 L 1151 584 L 1150 583 L 1150 581 L 1151 580 L 1151 567 L 1150 566 L 1150 552 L 1151 551 L 1151 549 L 1150 548 L 1151 511 L 1150 510 L 1150 493 L 1151 492 L 1151 471 L 1150 470 L 1150 460 L 1149 459 L 1148 452 L 1142 440 L 1134 431 L 1126 425 L 1122 423 L 1120 423 L 1115 420 L 1110 420 L 1109 419 L 960 419 L 959 418 L 949 417 L 948 416 L 937 413 L 927 408 L 925 406 L 916 401 L 903 388 L 896 378 L 890 366 L 888 356 L 887 355 L 887 352 L 886 351 L 886 345 L 885 344 L 885 166 L 881 156 L 875 150 L 868 146 L 865 146 L 864 145 L 576 145 L 575 146 L 563 147 L 562 148 L 559 148 L 558 149 L 555 149 L 554 150 Z" fill="#DDBE6E" fillRule="evenodd" />
    </svg>
  );

  const scrollToSource = (n: number) => {
    document.getElementById(`src-${n}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const sources = turns.length ? turns[turns.length - 1].sources : [];
  const canAsk = ready && !!activeId;

  return (
    <div className="h-full flex flex-col bg-surface">
      <header className="flex items-center gap-2.5 px-4 py-2.5 bg-brand text-white">
        <Mark />
        <h1 className="text-base font-medium tracking-wide">
          Cite<span className="text-accent-gold">Grain</span>
        </h1>
        <span className="text-xs text-white/55">{t.subtitle}</span>
        <button
          onClick={() => setLang(lang === "zh" ? "en" : "zh")}
          className="ml-auto rounded-lg border border-white/25 px-2 py-0.5 text-xs
                     text-white/80 hover:border-accent-gold hover:text-accent-gold
                     transition-colors"
          aria-label={lang === "zh" ? "Switch to English" : "切換為中文"}
        >
          {lang === "zh" ? "EN" : "中"}
        </button>
      </header>

      <div className="flex-1 min-h-0 flex">
        <DocumentPanel
          documents={documents} activeId={activeId} job={job} error={error} t={t}
          onSelect={select} onUpload={handleUpload} onUploadUrl={handleUploadUrl}
          onDelete={handleDelete}
        />
        <Chat
          turns={turns} quickQuestions={quick} ready={canAsk} busy={busy} t={t}
          onAsk={ask} onCite={scrollToSource}
        />
        <SourcePanel sources={sources} t={t} />
      </div>
    </div>
  );
}
