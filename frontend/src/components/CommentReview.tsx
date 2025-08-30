import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import axios from 'axios';

interface Comment {
  text: string;
  username: string;
  source: string;
  is_hate: boolean;
  confidence?: number;
  categories?: string[];
  explanation?: string;
  // PATCH: Enhanced metadata fields
  direct_url?: string;
  url_confidence?: number;
  url_source?: string;
  enhanced_metadata?: {
    thread_level?: number;
    parent_comment_id?: string;
    parent_username?: string;
    is_reply?: boolean;
    has_replies?: boolean;
    reply_count?: number;
    relative_indent?: number;
  };
}

interface ReviewData {
  scan_id: string;
  pending_comments: Comment[];
  total_count: number;
}

const CommentReview = () => {
  const { scanId } = useParams<{ scanId: string }>();
  const navigate = useNavigate();
  const [reviewData, setReviewData] = useState<ReviewData | null>(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [processing, setProcessing] = useState(false);
  const [approvedCount, setApprovedCount] = useState(0);
  const [rejectedCount, setRejectedCount] = useState(0);

  useEffect(() => {
    if (!scanId) {
      setError('No scan ID provided');
      return;
    }

    const fetchReviewData = async () => {
      try {
        const response = await axios.get<ReviewData>(`http://localhost:8000/api/scan/${scanId}/pending-review`);
        setReviewData(response.data);
        setLoading(false);
      } catch (err: any) {
        setError(err.response?.data?.detail || 'Failed to load review data');
        setLoading(false);
      }
    };

    fetchReviewData();
  }, [scanId]);

  const handleDecision = async (approved: boolean) => {
    if (!reviewData || processing) return;

    setProcessing(true);
    try {
      await axios.post(`http://localhost:8000/api/scan/${scanId}/review-comment`, {
        comment_index: currentIndex,
        approved: approved
      });

      if (approved) {
        setApprovedCount(prev => prev + 1);
      } else {
        setRejectedCount(prev => prev + 1);
      }

      // Move to next comment or complete review
      if (currentIndex < reviewData.pending_comments.length - 1) {
        setCurrentIndex(prev => prev + 1);
      } else {
        // All comments reviewed, complete the process
        await axios.post(`http://localhost:8000/api/scan/${scanId}/complete-review`);
        navigate(`/scan-status`, { 
          state: { 
            scanId: scanId,
            reviewComplete: true,
            approvedCount: approved ? approvedCount + 1 : approvedCount,
            rejectedCount: approved ? rejectedCount : rejectedCount + 1
          }
        });
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to process decision');
    } finally {
      setProcessing(false);
    }
  };

  if (loading) {
    return <div className="flex justify-center items-center h-64">Loading review data...</div>;
  }

  if (error) {
    return <div className="text-red-500 text-center p-4">{error}</div>;
  }

  if (!reviewData || reviewData.pending_comments.length === 0) {
    return <div className="text-center p-4">No comments to review</div>;
  }

  const currentComment = reviewData.pending_comments[currentIndex];
  const progress = ((currentIndex) / reviewData.pending_comments.length) * 100;

  return (
    <div className="max-w-4xl mx-auto p-6">
      <div className="bg-white shadow rounded-lg">
        <div className="px-6 py-4 border-b border-gray-200">
          <h1 className="text-2xl font-bold text-gray-900">Comment Review</h1>
          <div className="mt-2 flex justify-between items-center">
            <span className="text-sm text-gray-600">
              Comment {currentIndex + 1} of {reviewData.pending_comments.length}
            </span>
            <span className="text-sm text-gray-600">
              Approved: {approvedCount} | Rejected: {rejectedCount}
            </span>
          </div>
          
          {/* Progress bar */}
          <div className="mt-3 w-full bg-gray-200 rounded-full h-2">
            <div 
              className="bg-blue-600 h-2 rounded-full transition-all duration-300" 
              style={{ width: `${progress}%` }}
            ></div>
          </div>
        </div>

        <div className="p-6">
          {/* Comment Details */}
          <div className="mb-6">
            <div className="flex items-center mb-4">
              <div className="w-12 h-12 bg-gray-300 rounded-full flex items-center justify-center">
                <span className="text-lg font-medium text-gray-700">
                  {currentComment.username?.charAt(0)?.toUpperCase() || 'U'}
                </span>
              </div>
              <div className="ml-4">
                <h3 className="text-lg font-medium text-gray-900">{currentComment.username || 'Unknown User'}</h3>
                <div className="flex items-center space-x-2">
                  <p className="text-sm text-gray-500">Source: {currentComment.source}</p>
                  {/* PATCH: Thread hierarchy badge */}
                  {currentComment.enhanced_metadata?.thread_level !== undefined && (
                    <span className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${
                      currentComment.enhanced_metadata.thread_level === 0 
                        ? 'bg-blue-100 text-blue-800' 
                        : 'bg-gray-100 text-gray-800'
                    }`}>
                      {currentComment.enhanced_metadata.thread_level === 0 ? 'Main' : `Reply L${currentComment.enhanced_metadata.thread_level}`}
                    </span>
                  )}
                  {/* PATCH: URL confidence badge */}
                  {currentComment.url_confidence && currentComment.url_confidence > 0 && (
                    <span className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${
                      currentComment.url_confidence >= 0.7 
                        ? 'bg-green-100 text-green-800' 
                        : currentComment.url_confidence >= 0.5 
                        ? 'bg-yellow-100 text-yellow-800' 
                        : 'bg-red-100 text-red-800'
                    }`}>
                      URL {(currentComment.url_confidence * 100).toFixed(0)}%
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* PATCH: Thread context for replies */}
            {currentComment.enhanced_metadata?.is_reply && currentComment.enhanced_metadata?.parent_username && (
              <div className="bg-blue-50 border-l-4 border-blue-200 p-3 mb-4">
                <p className="text-sm text-blue-800">
                  <strong>Replying to:</strong> {currentComment.enhanced_metadata.parent_username}
                  {currentComment.enhanced_metadata.reply_count && currentComment.enhanced_metadata.reply_count > 0 && (
                    <span className="ml-2 text-xs text-blue-600">
                      ({currentComment.enhanced_metadata.reply_count} replies)
                    </span>
                  )}
                </p>
              </div>
            )}

            {/* Comment Content */}
            <div className="bg-gray-50 rounded-lg p-4 mb-4">
              <div className="flex justify-between items-start mb-2">
                <h4 className="text-sm font-medium text-gray-700">Comment Text:</h4>
                {/* PATCH: Direct URL link */}
                {currentComment.direct_url && (
                  <a
                    href={currentComment.direct_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center px-3 py-1 rounded-md text-xs font-medium bg-blue-100 text-blue-800 hover:bg-blue-200 transition-colors"
                  >
                    <svg className="w-3 h-3 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                    </svg>
                    Direct Link
                  </a>
                )}
              </div>
              <p className="text-gray-900 whitespace-pre-wrap">{currentComment.text}</p>
              {/* PATCH: URL source info */}
              {currentComment.url_source && (
                <p className="text-xs text-gray-500 mt-2">
                  URL extracted via: {currentComment.url_source}
                </p>
              )}
            </div>

            {/* AI Analysis */}
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
              <h4 className="text-sm font-medium text-red-800 mb-2">AI Analysis:</h4>
              <div className="space-y-2">
                <p className="text-sm text-red-700">
                  <strong>Confidence:</strong> {currentComment.confidence ? (currentComment.confidence * 100).toFixed(1) + '%' : 'N/A'}
                </p>
                {currentComment.categories && currentComment.categories.length > 0 && (
                  <p className="text-sm text-red-700">
                    <strong>Categories:</strong> {currentComment.categories.join(', ')}
                  </p>
                )}
                <p className="text-sm text-red-700">
                  <strong>Explanation:</strong> {currentComment.explanation || 'No explanation provided'}
                </p>
              </div>
            </div>
          </div>

          {/* Decision Buttons */}
          <div className="flex space-x-4">
            <button
              onClick={() => handleDecision(false)}
              disabled={processing}
              className="flex-1 bg-gray-600 text-white px-6 py-3 rounded-lg hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-gray-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {processing ? 'Processing...' : 'Reject - No Evidence'}
            </button>
            <button
              onClick={() => handleDecision(true)}
              disabled={processing}
              className="flex-1 bg-red-600 text-white px-6 py-3 rounded-lg hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {processing ? 'Processing...' : 'Approve - Create Evidence'}
            </button>
          </div>

          {/* Help text */}
          <div className="mt-6 bg-blue-50 border border-blue-200 rounded-lg p-4">
            <p className="text-sm text-blue-800">
              <strong>Review carefully:</strong> Approving will create permanent evidence files that can be used in legal proceedings. 
              Only approve if you believe this content violates community standards or laws.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default CommentReview;



